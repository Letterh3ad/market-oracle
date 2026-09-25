"""A fix run: one headless claude per trigger, locked down, under a time limit. `supervise` runs
in its own detached process (`python -m holiday_watch fix-run FILE`) so the pass never waits."""

from __future__ import annotations

import json
from pathlib import Path
from string import Template

from holiday_watch.config import DEFAULT_FILE, HERE, Config
from holiday_watch.seams import Watch
from holiday_watch.state import clear_lock, read_lock


def hw_command(cfg: Config) -> str:
    return f"{cfg.python.as_posix()} -m holiday_watch"


def fix_prompt(cfg: Config, trigger: dict) -> str:
    text = (HERE / "fix_prompt.md").read_text(encoding="utf-8")
    return Template(text).substitute(
        kind=trigger["kind"], detail=trigger["detail"], at=trigger["at"], minutes=f"{cfg.fix_timeout_min:g}",
        repo=cfg.repo, live=cfg.live, reports=cfg.reports, app_url=cfg.app_url, hw=hw_command(cfg),
        python=cfg.python.as_posix())


def _rules(tool: str, path: Path) -> list[str]:
    """An absolute-path permission rule, in both spellings a Windows drive may take."""
    posix = path.resolve().as_posix()
    drive, _, rest = posix.partition(":")
    forms = [f"//{posix}"] + ([f"//{drive.lower()}{rest}"] if rest else [])
    return [f"{tool}({form}/**)" for form in forms]


def fix_settings(cfg: Config) -> dict:
    """Allowed: read anything, act only through the holiday_watch command, edit only the
    holiday-fix worktree, commit there and run its tests. dontAsk refuses everything else."""
    fix_tree = cfg.repo / ".claude" / "worktrees" / "holiday-fix"
    allow = ["Read", "Grep", "Glob", f"Bash({hw_command(cfg)}:*)", f"Bash({cfg.python.as_posix()} -m pytest:*)",
             "Bash(cd:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
             "Bash(git branch:*)", "Bash(git worktree add:*)", "Bash(git worktree list:*)", "Bash(git add:*)",
             "Bash(git commit:*)", *_rules("Edit", fix_tree), *_rules("Write", fix_tree)]
    deny = ["Bash(git push:*)", "Bash(git merge:*)", "Bash(git reset:*)", "Bash(git rebase:*)",
            "Bash(git checkout:*)", "Bash(git switch:*)", "Bash(schtasks:*)", "Bash(taskkill:*)",
            "Edit(**/oracle.toml)", "Write(**/oracle.toml)", "Edit(**/*.db)", "Write(**/*.db)",
            *_rules("Edit", cfg.live), *_rules("Write", cfg.live), "WebFetch", "WebSearch"]
    return {"permissions": {"allow": allow, "deny": deny, "defaultMode": "dontAsk"}, "disableAllHooks": True}


def fix_command(cfg: Config, settings: Path) -> tuple[str, ...]:
    return (cfg.claude, "-p", "--model", cfg.fix_model, "--no-session-persistence", "--settings", str(settings),
            "--setting-sources", "project", "--strict-mcp-config", "--permission-mode", "dontAsk",
            "--output-format", "text")


def supervise(w: Watch, trigger_file: Path) -> int:
    cfg = w.cfg
    trigger = json.loads(trigger_file.read_text(encoding="utf-8"))
    cfg.fixruns.mkdir(parents=True, exist_ok=True)
    settings = cfg.fixruns / "settings.json"
    settings.write_text(json.dumps(fix_settings(cfg), indent=1), encoding="utf-8")
    try:
        # The run's own `holiday_watch` commands read this watch's settings from any folder.
        env = {"HOLIDAY_WATCH_CONFIG": str((w.config_path or DEFAULT_FILE).resolve())}
        ran = w.system.run(fix_command(cfg, settings), fix_prompt(cfg, trigger), cfg.fix_timeout_min * 60, cfg.repo,
                           env=env)
        out = cfg.fixruns / trigger_file.with_suffix(".md").name
        how = f"killed after {cfg.fix_timeout_min:g} min" if ran.timed_out else f"finished (exit {ran.code})"
        out.write_text(f"# Fix run: {trigger['kind']}\n\nTrigger: {trigger['detail']}\nRaised: {trigger['at']}\n"
                       f"Ended: {w.now().isoformat(timespec='seconds')}, {how}\n\n{ran.out.strip()}\n",
                       encoding="utf-8")
        w.log(f"fix run for {trigger['kind']} {how}; account in {out}")
        return 1 if ran.timed_out or ran.code else 0
    finally:
        lock = read_lock(cfg.lock_file)
        if lock and lock.get("trigger_file") == str(trigger_file):
            clear_lock(cfg.lock_file)
