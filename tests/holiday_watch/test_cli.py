import json
import sqlite3
import pytest

from holiday_watch.cli import api_allowed, main
from holiday_watch.seams import dry
from holiday_watch.state import write_lock

from tests.holiday_watch.fakes import APP, NOW, FakeHttp, FakeSystem, healthy_routes, proc, watch


def run(w, *argv):
    made = []

    def factory(config_path, dry_run):
        if dry_run:
            dry(w)
        made.append(w)
        return w
    code = main(list(argv), make_watch=factory)
    return code, "\n".join(w.lines)


@pytest.mark.parametrize("method,path,ok", [
    ("GET", "/api/replay", True), ("GET", "/api/activity/finished", True), ("GET", "/other", False),
    ("GET", "/api/../x", False), ("POST", "/api/replay/retry", True), ("POST", "/api/replay/pause", True),
    ("POST", "/api/replay/start", True), ("POST", "/api/activity/12/cancel", True),
    ("POST", "/api/activity/12/retry", True), ("POST", "/api/activity/retry-all", True),
    ("POST", "/api/app/restart", True), ("POST", "/api/app/shutdown", False), ("POST", "/api/replay", False),
    ("POST", "/api/settings", False), ("DELETE", "/api/replay", False),
])
def test_api_allowlist(method, path, ok):
    assert api_allowed(method, path) is ok


def test_api_get_prints_the_answer(tmp_path):
    w = watch(tmp_path)
    code, out = run(w, "api", "GET", "/api/activity")
    assert code == 0 and '"jobs": []' in out


def test_api_post_sends_the_body_and_refuses_what_is_not_allowed(tmp_path):
    http = FakeHttp(healthy_routes())
    w = watch(tmp_path, http=http)
    assert run(w, "api", "POST", "/api/replay/retry", '{"cell_id": 4}')[0] == 0
    assert http.posts == [(f"{APP}/api/replay/retry", {"cell_id": 4})]
    code, out = run(w, "api", "POST", "/api/app/shutdown")
    assert code == 2 and "not allowed" in out and len(http.posts) == 1


def test_restart_app_asks_a_running_app_to_restart(tmp_path):
    http = FakeHttp(healthy_routes())
    system = FakeSystem()
    w = watch(tmp_path, http=http, system=system)
    assert run(w, "restart-app")[0] == 0
    assert http.posts == [(f"{APP}/api/app/restart", None)] and system.spawned == []


def test_restart_app_starts_a_down_app(tmp_path):
    system = FakeSystem()
    w = watch(tmp_path, http=FakeHttp({}), system=system)
    run(w, "restart-app")
    assert system.spawned[0][0] == w.cfg.app_start


def test_sql_is_read_only(tmp_path):
    db = tmp_path / "o.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE t (a)")
        c.execute("INSERT INTO t VALUES (1), (2)")
    c.close()
    w = watch(tmp_path, db=db)
    code, out = run(w, "sql", "SELECT a FROM t ORDER BY a")
    assert code == 0 and "a\n1\n2" in out
    code, out = run(w, "sql", "DELETE FROM t")
    assert code == 1 and "readonly" in out.lower().replace(" ", "")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 2
    c.close()


def test_status_prints_the_watch_state(tmp_path):
    w = watch(tmp_path)
    w.cfg.reports.mkdir(parents=True)
    w.cfg.state_file.write_text(json.dumps({"app_restarts": 1, "fix_runs": [
        {"kind": "long_job", "key": "long_job:7", "at": NOW.isoformat()}]}), encoding="utf-8")
    write_lock(w.cfg.lock_file, {"pid": 5, "started_at": NOW.isoformat(), "trigger_file": "x.json"})
    w.cfg.log_file.write_text("line one\nline two\n", encoding="utf-8")
    code, out = run(w, "status")
    assert code == 0
    for piece in ("App: up", "Ollama: up", "App restarts in a row: 1", "Fix runs today: 1 of 3",
                  "long_job", "Fix run running: PID 5", "line two"):
        assert piece in out, piece


def test_pass_dry_run_uses_the_dry_system(tmp_path):
    system = FakeSystem()
    w = watch(tmp_path, http=FakeHttp({}), system=system)
    code, out = run(w, "pass", "--dry-run")
    assert code == 0 and system.spawned == [] and "[dry-run] would start" in out


def test_an_unexpected_error_is_logged(tmp_path):
    class Broken(FakeSystem):
        def processes(self):
            raise RuntimeError("boom")
    w = watch(tmp_path, http=FakeHttp({}), system=Broken())
    code, out = run(w, "restart-app")
    assert code == 1 and "restart-app failed: RuntimeError('boom')" in w.cfg.log_file.read_text(encoding="utf-8")


def test_a_fix_run_may_not_pass_its_own_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLIDAY_WATCH_FIXRUN", "1")
    w = watch(tmp_path)
    code, out = run(w, "status", "--config", str(tmp_path / "evil.toml"))
    assert code == 2 and "--config" in out


def test_fix_run_supervises(tmp_path):
    system = FakeSystem()
    w = watch(tmp_path, system=system)
    w.cfg.fixruns.mkdir(parents=True)
    f = w.cfg.fixruns / "t.json"
    f.write_text(json.dumps({"kind": "k", "key": "k:1", "detail": "d", "at": "a"}), encoding="utf-8")
    assert run(w, "fix-run", str(f))[0] == 0 and len(system.ran) == 1


def test_report_command_writes_the_file(tmp_path):
    w = watch(tmp_path, system=FakeSystem())
    w.cfg.reports.mkdir(parents=True)
    assert run(w, "report")[0] == 0
    assert (w.cfg.reports / "2026-09-26.md").exists()



def real_files(reports):
    return sorted(p.relative_to(reports).as_posix() for p in reports.rglob("*")
                  if p.is_file() and "dry-run" not in p.parts)


@pytest.mark.parametrize("argv,up", [
    (["pass"], False), (["pass"], True), (["report"], True), (["status"], True), (["restart-app"], True),
    (["restart-app"], False), (["start-ollama"], False), (["api", "POST", "/api/replay/start"], True),
    (["api", "POST", "/api/replay/retry", '{"cell_id": 1}'], True), (["api", "GET", "/api/replay"], True),
    (["fix-run", "TRIGGER"], True),
])
def test_dry_run_posts_starts_kills_and_runs_nothing(tmp_path, argv, up):
    http = FakeHttp(healthy_routes() if up else {})
    system = FakeSystem([proc(800, cmd="claude.exe -p x", age_min=500),
                         proc(321, name="python.exe", cmd="python -m oracle gui")])
    w = watch(tmp_path, http=http, system=system)
    reports = w.cfg.reports
    reports.mkdir(parents=True)
    w.cfg.state_file.write_text(json.dumps({"server_pid": 321, "down_since": "2026-09-26T02:00:00Z"}),
                                encoding="utf-8")
    trigger = tmp_path / "t.json"
    trigger.write_text(json.dumps({"kind": "k", "key": "k:1", "detail": "d", "at": "a"}), encoding="utf-8")
    before = real_files(reports)
    code, out = run(w, *[str(trigger) if a == "TRIGGER" else a for a in argv], "--dry-run")
    assert code == 0, out
    assert http.posts == [] and system.spawned == [] and system.killed == [] and system.ran == []
    assert real_files(reports) == before
    if argv[0] in ("restart-app", "api") and argv[1:2] != ["GET"]:
        assert "[dry-run] would" in out
