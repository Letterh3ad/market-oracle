"""One watchdog pass: keep the app, Ollama and the Remote Control session up, start a fix run
when something needs one, and reap stray claude processes."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta

from holiday_watch.procs import Proc
from holiday_watch.reaper import choose_kills, is_claude, protected_pids, track_cpu
from holiday_watch.seams import HttpError, Watch
from holiday_watch.state import (clear_lock, load_state, read_lock, read_pid, save_state, write_lock,
                                 write_pid)
from holiday_watch.timeutil import iso, parse
from holiday_watch.triggers import Trigger, find_triggers, gate, track_replay

LOCK_SLACK = timedelta(minutes=5)
"""Past the fix-run limit plus this, the supervisor failed to stop its run: the pass does."""


def run_pass(w: Watch) -> None:
    cfg, now = w.cfg, w.now()
    state_file = cfg.state_file(w.dry_run)
    state = load_state(state_file)
    status = _get(w, "/api/status", quiet=True)
    app_up = status is not None
    if app_up:
        if state.get("app_restarts"):
            w.log(f"app is back up after {state['app_restarts']} restart(s)")
        state["app_restarts"] = 0
        state.pop("down_since", None)
        state["server_pid"] = (status.get("server") or {}).get("pid")
    else:
        state.setdefault("down_since", iso(now))
    ensure_ollama(w)
    procs = w.system.processes()
    rc_pid = ensure_remote_control(w, procs)
    lock = _check_lock(w, procs, now)

    replay = activity = None
    if app_up:
        replay, activity = _get(w, "/api/replay"), _get(w, "/api/activity")
        track_replay(state, now, replay)
    for t in find_triggers(cfg, now, state, app_up=app_up, status=status, replay=replay, activity=activity,
                           tz=w.tz):
        held = gate(cfg, now, state, t, lock_busy=lock is not None, tz=w.tz)
        if held:
            w.log(f"trigger {t.kind} held: {held} ({t.detail})")
        else:
            lock = start_fix_run(w, state, t)

    if not app_up:
        restart_app(w, state.get("server_pid"), procs)
        state["app_restarts"] = state.get("app_restarts", 0) + 1

    state["cpu"] = track_cpu(state.get("cpu", {}), procs, now, cfg)
    protected = protected_pids(procs, now, cfg, rc_pid=rc_pid, lock=lock, own_pid=w.own_pid)
    for k in choose_kills(procs, now, state["cpu"], protected, cfg):
        answer = w.system.kill_tree(k.proc.pid)
        age = f"{k.age_s / 60:.0f} min" if k.age_s is not None else "unknown"
        w.log(f"reaped claude PID {k.proc.pid} ({k.reason}), age {age}, command: {k.proc.cmdline}: {answer}")
    save_state(state_file, state, now)


def _get(w: Watch, path: str, quiet: bool = False) -> dict | None:
    try:
        return w.http.get(w.api(path))
    except HttpError as exc:
        if not quiet:
            w.log(f"GET {path} failed: {exc}")
        return None


def restart_app(w: Watch, server_pid: int | None, procs: Sequence[Proc]) -> None:
    """Start the app from the live worktree; a server that still holds its PID but does not
    answer is killed first, or the new one could not bind the port."""
    hung = next((p for p in procs if p.pid == server_pid), None)
    if hung and "oracle" in hung.cmdline and "gui" in hung.cmdline:
        w.log(f"app server PID {hung.pid} does not answer: killing it: {w.system.kill_tree(hung.pid)}")
    pid = w.system.spawn(w.cfg.app_start, w.cfg.live, log=w.cfg.reports / "app-start.log")
    w.log(f"app down ({w.api('/api/status')} failed): started it from {w.cfg.live} (PID {pid})")


def ensure_ollama(w: Watch) -> None:
    try:
        w.http.get(f"{w.cfg.ollama_url}/api/tags")
    except HttpError as exc:
        pid = w.system.spawn(w.cfg.ollama_start, w.cfg.repo, log=w.cfg.reports / "ollama.log")
        w.log(f"Ollama down ({exc}): started {' '.join(w.cfg.ollama_start)} (PID {pid})")


def ensure_remote_control(w: Watch, procs: Sequence[Proc]) -> int | None:
    """The allowed Remote Control session's PID: the one in the PID file, else an existing
    session adopted, else a new one launched in a console."""
    cfg = w.cfg
    if not cfg.rc_launch:
        return None
    by_pid = {p.pid: p for p in procs}
    pid = read_pid(cfg.rc_pid_file)
    if pid in by_pid and cfg.rc_marker in by_pid[pid].cmdline:
        return pid
    sessions = sorted((p for p in procs if is_claude(p, cfg.reaper_match) and cfg.rc_marker in p.cmdline),
                      key=lambda p: p.created or datetime.max.replace(tzinfo=w.now().tzinfo))
    if sessions:
        if not w.dry_run:
            write_pid(cfg.rc_pid_file, sessions[0].pid)
        w.log(f"Remote Control session PID {sessions[0].pid} adopted as the allowed one")
        return sessions[0].pid
    new = w.system.spawn(cfg.rc_launch, cfg.repo, console=True)
    if new is not None:
        write_pid(cfg.rc_pid_file, new)
    w.log(f"Remote Control session gone (PID file said {pid}): relaunched it (PID {new})")
    return new


def _check_lock(w: Watch, procs: Sequence[Proc], now: datetime) -> dict | None:
    """The running fix run's lock, or None once it is gone, stale or overrun."""
    lock = read_lock(w.cfg.lock_file)
    if not lock:
        return None
    if lock["pid"] not in {p.pid for p in procs}:
        w.log(f"fix-run lock of PID {lock['pid']} cleared: the process is gone")
        clear_lock(w.cfg.lock_file)
        return None
    if now - parse(lock["started_at"]) > timedelta(minutes=w.cfg.fix_timeout_min) + LOCK_SLACK:
        answer = w.system.kill_tree(lock["pid"])
        w.log(f"fix run PID {lock['pid']} past {w.cfg.fix_timeout_min:g} min: killed its tree: {answer}")
        clear_lock(w.cfg.lock_file)
        return None
    return lock


def start_fix_run(w: Watch, state: dict, t: Trigger) -> dict | None:
    """Hand the trigger to a detached supervisor (`fix-run`), which runs claude under the time
    limit. Its lock, or None in a dry run."""
    now = w.now()
    w.cfg.fixruns.mkdir(parents=True, exist_ok=True)
    stem = w.cfg.fixruns / f"{now.astimezone(w.tz):%Y-%m-%d-%H%M}-{t.kind}"
    trigger_file = stem.with_suffix(".json")
    trigger_file.write_text(json.dumps({"kind": t.kind, "key": t.key, "detail": t.detail, "at": iso(now)},
                                       indent=1), encoding="utf-8")
    args = [sys.executable, "-m", "holiday_watch", "fix-run", str(trigger_file)]
    if w.config_path:
        args += ["--config", str(w.config_path)]
    pid = w.system.spawn(args, w.cfg.repo)
    state.setdefault("fix_runs", []).append({"kind": t.kind, "key": t.key, "at": iso(now)})
    state.setdefault("fired", {})[t.key] = iso(now)
    w.log(f"fix run started for {t.kind} (PID {pid}, output {stem.with_suffix('.md')}): {t.detail}")
    if pid is None:
        return None
    lock = {"pid": pid, "started_at": iso(now), "trigger_file": str(trigger_file)}
    write_lock(w.cfg.lock_file, lock)
    return lock
