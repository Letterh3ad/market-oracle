import json
from datetime import timedelta

from holiday_watch.seams import DryRunSystem, HttpError
from holiday_watch.state import load_state, read_lock
from holiday_watch.watchdog import run_pass

from tests.holiday_watch.fakes import APP, NOW, OLLAMA, FakeHttp, FakeSystem, healthy_routes, proc, watch

RC = "claude.exe --remote-control holiday"


def with_rc(tmp_path, procs=(), **kw):
    """A watch whose Remote Control session is alive and known by its PID file."""
    system = FakeSystem([proc(700, cmd=RC, age_min=600), *procs])
    w = watch(tmp_path, system=system, **kw)
    w.cfg.reports.mkdir(parents=True, exist_ok=True)
    w.cfg.rc_pid_file.write_text("700", encoding="utf-8")
    return w, system


def test_a_healthy_pass_does_nothing_and_remembers_the_server(tmp_path):
    w, system = with_rc(tmp_path)
    run_pass(w)
    assert system.spawned == [] and system.killed == []
    state = load_state(w.cfg.state_file())
    assert state["server_pid"] == 321 and state["app_restarts"] == 0


def test_app_down_is_restarted_from_the_live_worktree_then_a_fix_run_after_two(tmp_path):
    http = FakeHttp({k: v for k, v in healthy_routes().items() if "/api/status" not in k})
    w, system = with_rc(tmp_path, http=http)
    run_pass(w)
    assert system.spawned == [(w.cfg.app_start, w.cfg.live, False)]
    assert load_state(w.cfg.state_file())["app_restarts"] == 1
    run_pass(w)
    assert load_state(w.cfg.state_file())["app_restarts"] == 2
    run_pass(w)  # still down after two restarts: restart again and start a fix run
    kinds = [s[0][2:4] for s in system.spawned]
    assert ("holiday_watch", "fix-run") in kinds and system.spawned[-1][0] == w.cfg.app_start
    lock = read_lock(w.cfg.lock_file)
    assert lock["pid"] == system.next_pid - 1
    trigger = json.loads(open(lock["trigger_file"], encoding="utf-8").read())
    assert trigger["kind"] == "app_down"
    state = load_state(w.cfg.state_file())
    assert [r["kind"] for r in state["fix_runs"]] == ["app_down"] and trigger["key"] in state["fired"]
    assert any("fix run started" in line for line in w.lines)


def test_the_app_coming_back_resets_the_restart_count(tmp_path):
    w, _ = with_rc(tmp_path)
    w.cfg.reports.mkdir(parents=True, exist_ok=True)
    w.cfg.state_file().write_text(json.dumps({"app_restarts": 2, "down_since": "x"}), encoding="utf-8")
    run_pass(w)
    state = load_state(w.cfg.state_file())
    assert state["app_restarts"] == 0 and "down_since" not in state
    assert any("back up" in line for line in w.lines)


def test_a_hung_server_is_killed_before_the_restart(tmp_path):
    http = FakeHttp({k: v for k, v in healthy_routes().items() if "/api/status" not in k})
    hung = proc(321, name="python.exe", cmd="python -m oracle --config x gui")
    w, system = with_rc(tmp_path, procs=[hung], http=http)
    w.cfg.state_file().write_text(json.dumps({"server_pid": 321}), encoding="utf-8")
    run_pass(w)
    assert system.killed == [321]
    assert system.spawned[0][0] == w.cfg.app_start


def test_a_reused_server_pid_is_not_killed(tmp_path):
    http = FakeHttp({k: v for k, v in healthy_routes().items() if "/api/status" not in k})
    other = proc(321, name="notepad.exe", cmd="notepad")
    w, system = with_rc(tmp_path, procs=[other], http=http)
    w.cfg.state_file().write_text(json.dumps({"server_pid": 321}), encoding="utf-8")
    run_pass(w)
    assert system.killed == []


def test_ollama_down_is_started(tmp_path):
    http = FakeHttp({k: v for k, v in healthy_routes().items() if OLLAMA not in k})
    w, system = with_rc(tmp_path, http=http)
    run_pass(w)
    assert system.spawned == [(w.cfg.ollama_start, w.cfg.repo, False)]


def test_a_dead_remote_control_session_is_relaunched_in_a_console(tmp_path):
    system = FakeSystem()
    w = watch(tmp_path, system=system)
    run_pass(w)
    assert system.spawned == [(w.cfg.rc_launch, w.cfg.repo, True)]
    assert w.cfg.rc_pid_file.read_text(encoding="utf-8") == str(system.next_pid)


def test_an_unknown_remote_control_session_is_adopted_not_doubled(tmp_path):
    system = FakeSystem([proc(710, cmd=RC, age_min=20), proc(711, cmd=RC, age_min=5)])
    w = watch(tmp_path, system=system)
    run_pass(w)
    assert system.spawned == []
    assert w.cfg.rc_pid_file.read_text(encoding="utf-8") == "710"


def test_the_reaper_kills_idle_and_old_claude_but_spares_remote_control(tmp_path):
    old = proc(800, cmd="claude.exe -p something", age_min=200)
    w, system = with_rc(tmp_path, procs=[old])
    run_pass(w)
    assert system.killed == [800]
    line = next(line for line in w.lines if "PID 800" in line)
    assert "past its 3 h time limit" in line and "age 200 min" in line and "claude.exe -p something" in line


def test_idle_claude_is_reaped_on_a_later_pass(tmp_path):
    idle = proc(810, cmd="claude.exe", age_min=40, cpu=3.0)
    w, system = with_rc(tmp_path, procs=[idle], now=NOW - timedelta(minutes=31))
    run_pass(w)
    assert system.killed == []
    w.now = lambda: NOW
    run_pass(w)
    assert system.killed == [810]


def test_a_stale_lock_is_cleared_and_an_overrun_fix_run_killed(tmp_path):
    w, system = with_rc(tmp_path)
    w.cfg.lock_file.write_text(json.dumps({"pid": 900, "started_at": NOW.isoformat()}), encoding="utf-8")
    run_pass(w)
    assert not w.cfg.lock_file.exists() and system.killed == []
    system.procs.append(proc(901, name="python.exe", cmd="python -m holiday_watch fix-run x", age_min=40))
    w.cfg.lock_file.write_text(json.dumps({"pid": 901, "started_at": (NOW - timedelta(minutes=36)).isoformat()}),
                               encoding="utf-8")
    run_pass(w)
    assert system.killed == [901] and not w.cfg.lock_file.exists()


def test_a_running_fix_run_holds_new_triggers(tmp_path):
    routes = healthy_routes()
    routes[f"{APP}/api/replay"] = {"study": {"id": 1, "state": "running", "done": 7, "failed": [{}, {}],
                                             "top_failures": [], "finished": False}}
    w, system = with_rc(tmp_path, procs=[proc(902, name="python.exe", cmd="python fix-run")], http=FakeHttp(routes))
    w.cfg.lock_file.write_text(json.dumps({"pid": 902, "started_at": NOW.isoformat()}), encoding="utf-8")
    run_pass(w)
    assert system.spawned == []
    assert any("replay_failures held: a fix run is still running" in line for line in w.lines)


def test_dry_run_only_says_what_it_would_do(tmp_path):
    http = FakeHttp({k: v for k, v in healthy_routes().items() if "/api/status" not in k and OLLAMA not in k})
    real = FakeSystem([proc(800, cmd="claude.exe -p x", age_min=500)])
    w = watch(tmp_path, http=http, system=real)
    w.dry_run = True
    w.system = DryRunSystem(say=w.log, real=real)
    run_pass(w)
    assert real.spawned == [] and real.killed == []
    text = w.cfg.log_file.read_text(encoding="utf-8")
    assert "[dry-run] would start" in text and "[dry-run] would kill the process tree of PID 800" in text
    assert w.cfg.state_file(dry_run=True).exists() and not w.cfg.state_file().exists()
    assert not w.cfg.rc_pid_file.exists() and not w.cfg.lock_file.exists()


def test_an_app_api_error_after_status_is_logged_not_fatal(tmp_path):
    routes = healthy_routes()
    routes[f"{APP}/api/replay"] = HttpError("boom")
    w, system = with_rc(tmp_path, http=FakeHttp(routes))
    run_pass(w)
    assert any("boom" in line for line in w.lines)
