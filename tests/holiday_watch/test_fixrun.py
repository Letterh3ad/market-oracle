import json
from datetime import timedelta

from holiday_watch.fixrun import fix_command, fix_prompt, fix_settings, supervise
from holiday_watch.seams import Ran
from holiday_watch.state import read_lock, write_lock

from tests.holiday_watch.fakes import NOW, FakeSystem, watch

TRIGGER = {"kind": "long_job", "key": "long_job:7", "detail": "job 7 (Research X) running since ...",
           "at": "2026-09-26T03:00:00Z"}


def trigger_file(w):
    w.cfg.fixruns.mkdir(parents=True, exist_ok=True)
    path = w.cfg.fixruns / "2026-09-26-0300-long_job.json"
    path.write_text(json.dumps(TRIGGER), encoding="utf-8")
    return path


def test_the_prompt_carries_the_trigger_the_commands_and_the_rules(tmp_path):
    w = watch(tmp_path)
    text = fix_prompt(w.cfg, TRIGGER)
    assert "long_job" in text and "job 7 (Research X)" in text
    assert f"{w.cfg.python.as_posix()} -P -m holiday_watch api POST /api/replay/retry" in text
    assert "oracle.db" in text and "never open" in text
    assert "NEVER deploy" in text and str(w.cfg.live) in text and "30 minutes" in text
    assert "$" not in text.replace("$ ", "")  # every placeholder filled


def test_settings_lock_the_run_down(tmp_path):
    w = watch(tmp_path)
    s = fix_settings(w.cfg)
    allow, deny = s["permissions"]["allow"], s["permissions"]["deny"]
    assert s["permissions"]["defaultMode"] == "dontAsk" and s["disableAllHooks"] is True
    hw = f"{w.cfg.python.as_posix()} -P -m holiday_watch"
    for rule in (f"Bash({hw} status)", f"Bash({hw} api:*)", f"Bash({hw} restart-app)", f"Bash({hw} start-ollama)",
                 f"Bash({hw} sql:*)"):
        assert rule in allow
    assert f"Bash({hw}:*)" not in allow and "Bash(*--config*)" in deny
    assert not any(a in ("Edit", "Write", "Bash") for a in allow)
    assert any("holiday-fix" in a and a.startswith("Edit(") for a in allow)
    # the watch's own code, settings and prompts are never edited, in any worktree
    for rule in ("Edit(**/holiday_watch/**)", "Write(**/holiday_watch/**)"):
        assert rule in deny
    reports = w.cfg.reports.resolve().as_posix()
    assert any(reports.split(":", 1)[-1] in d and d.startswith("Write(") for d in deny)
    git = sorted(a for a in allow if a.startswith("Bash(git"))
    tree = (w.cfg.repo / ".claude/worktrees/holiday-fix").as_posix()
    # add/commit only in the fix worktree: a commit in the main checkout would take the user's work
    assert git == sorted(["Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", f"Bash(git -C {tree} add:*)",
                          f"Bash(git -C {tree} commit:*)",
                          "Bash(git worktree add .claude/worktrees/holiday-fix -b holiday-fix integration)",
                          "Bash(git worktree add .claude/worktrees/holiday-fix holiday-fix)"])
    live = w.cfg.live.resolve().as_posix()
    for rule in ("Bash(*--output*)", "Bash(git push:*)", "Bash(git merge:*)", "Bash(schtasks:*)", "Edit(**/oracle.toml)",
                 "Write(**/*.db)"):
        assert rule in deny
    assert any(live.split(":", 1)[-1] in d and d.startswith("Edit(") for d in deny)


def test_the_claude_command_is_headless_and_locked(tmp_path):
    w = watch(tmp_path)
    args = fix_command(w.cfg, tmp_path / "s.json")
    assert args[:2] == (w.cfg.claude, "-p")
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--settings"):
        assert flag in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--model") + 1] == "opus"


def test_supervise_runs_claude_saves_its_account_and_frees_the_lock(tmp_path):
    system = FakeSystem(result=Ran(0, "Found a stuck job; cancelled job 7."))
    w = watch(tmp_path, system=system)
    path = trigger_file(w)
    # the lock holds the launcher's PID (a venv python.exe starts the real one as a child)
    write_lock(w.cfg.lock_file, {"pid": 4242, "started_at": NOW.isoformat(), "trigger_file": str(path)})
    assert supervise(w, path) == 0
    (args, stdin), = system.ran
    assert "job 7 (Research X)" in stdin
    # its holiday_watch commands find this watch's settings even from the holiday-fix worktree
    env = system.envs[0]
    assert env["HOLIDAY_WATCH_CONFIG"].endswith("config.toml") and env["HOLIDAY_WATCH_FIXRUN"] == "1"
    assert env["PYTHONPATH"] == str(w.cfg.repo)  # with -P, the repo's copy of the watch, whatever the cwd
    out = path.with_suffix(".md").read_text(encoding="utf-8")
    assert "cancelled job 7" in out and "long_job" in out
    assert read_lock(w.cfg.lock_file) is None
    assert json.loads((w.cfg.fixruns / "settings.json").read_text(encoding="utf-8"))["disableAllHooks"]
    assert any("fix run for long_job finished (exit 0)" in line for line in w.lines)


def test_a_timed_out_run_is_logged_as_killed(tmp_path):
    system = FakeSystem(result=Ran(1, "half way", timed_out=True))
    w = watch(tmp_path, system=system, now=NOW + timedelta(minutes=30))
    path = trigger_file(w)
    assert supervise(w, path) == 1
    assert any("killed after 30 min" in line for line in w.lines)
    assert "half way" in path.with_suffix(".md").read_text(encoding="utf-8")


def test_supervise_leaves_another_runs_lock_alone(tmp_path):
    w = watch(tmp_path)
    path = trigger_file(w)
    write_lock(w.cfg.lock_file, {"pid": 999, "started_at": NOW.isoformat(), "trigger_file": "other.json"})
    supervise(w, path)
    assert read_lock(w.cfg.lock_file)["pid"] == 999
