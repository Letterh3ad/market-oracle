"""One watchdog pass: keep the app, Ollama and the Remote Control session up, start a fix run
when something needs one, and reap stray claude processes. Each step runs on its own: a failure is
logged and the rest of the pass goes on."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import TypeVar

from holiday_watch.procs import Proc
from holiday_watch.reaper import choose_kills, is_claude, is_remote_control, protected_pids, track_cpu
from holiday_watch.seams import HttpError, Watch
from holiday_watch.state import (clear_lock, load_state, read_lock, read_pid, save_state, write_lock,
                                 write_pid)
from holiday_watch.timeutil import iso, parse
from holiday_watch.triggers import Trigger, find_triggers, gate, track_replay

T = TypeVar("T")

LOCK_SLACK = timedelta(minutes=5)
"""Past the fix-run limit plus this, the supervisor failed to stop its run: the pass does."""
STATUS_TRIES = 3
STATUS_TIMEOUT_S = 20.0
STATUS_GAP_S = 20.0
"""A busy server can take a while to answer: about a minute of tries before it counts as down."""
SAME_START = timedelta(minutes=10)
"""A process created this close to the server's reported start is that server."""
RUN_START_SLACK = timedelta(minutes=2)


def _step(w: Watch, name: str, fn: Callable[[], T], default: T) -> T:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 (one broken step must not stop the watch)
        w.log(f"step {name} failed: {exc!r}")
        return default


def run_pass(w: Watch) -> None:
    cfg, now = w.cfg, w.now()
    state = load_state(cfg.state_file)
    was_down = "down_since" in state
    try:
        status = _step(w, "app check", lambda: app_status(w), None)
        app_up = status is not None
        if app_up:
            if state.get("app_restarts"):
                w.log(f"app is back up after {state['app_restarts']} pass(es) down")
            state["app_restarts"] = 0
            state.pop("down_since", None)
            server = status.get("server") or {}
            state["server_pid"], state["server_started_at"] = server.get("pid"), server.get("started_at")
        else:
            state.setdefault("down_since", iso(now))
        _step(w, "ollama", lambda: ensure_ollama(w), None)
        procs = _step(w, "process list", w.system.processes, None)
        rc = _step(w, "remote control", lambda: ensure_remote_control(w, procs), set()) if procs is not None \
            else set()
        # Unreadable, a lock counts as a run still going: no second fix run, no kill.
        lock = _step(w, "fix-run lock", lambda: _check_lock(w, procs, now), read_lock(cfg.lock_file)) \
            if procs is not None else read_lock(cfg.lock_file)
        lock = _step(w, "triggers", lambda: _triggers(w, state, now, status, lock), lock)
        if not app_up:
            _step(w, "app restart", lambda: restart_app(w, state, procs or [], was_down), None)
            state["app_restarts"] = state.get("app_restarts", 0) + 1
        if procs is not None:
            _step(w, "reaper", lambda: _reap(w, state, procs, rc, lock, now), None)
        if getattr(w.system, "breakaway_refused", False):
            w.log("Task Scheduler refused CREATE_BREAKAWAY_FROM_JOB: started processes end with the task")
    finally:
        _step(w, "save state", lambda: save_state(cfg.state_file, state, now), None)


def _triggers(w: Watch, state: dict, now: datetime, status: dict | None, lock: dict | None) -> dict | None:
    replay = activity = None
    if status is not None:
        replay, activity = _get(w, "/api/replay"), _get(w, "/api/activity")
        track_replay(state, now, replay)
    for t in find_triggers(w.cfg, now, state, app_up=status is not None, status=status, replay=replay,
                           activity=activity, tz=w.tz):
        held = gate(w.cfg, now, state, t, lock_busy=lock is not None, tz=w.tz)
        if held:
            w.log(f"trigger {t.kind} held: {held} ({t.detail})")
        else:
            lock = _step(w, "fix-run launch", lambda t=t: start_fix_run(w, state, t), lock)
    return lock


def _reap(w: Watch, state: dict, procs: Sequence[Proc], rc: set[int], lock: dict | None, now: datetime) -> None:
    cfg = w.cfg
    state["cpu"] = track_cpu(state.get("cpu", {}), procs, now, cfg)
    protected = protected_pids(procs, now, cfg, rc_pids=rc, lock=lock, own_pid=w.own_pid,
                               server_pid=state.get("server_pid"))
    noted = {}
    for k in choose_kills(procs, now, state["cpu"], protected, cfg):
        age = f"{k.age_s / 60:.0f} min" if k.age_s is not None else "unknown"
        what = f"claude PID {k.proc.pid} ({k.reason}), age {age}, command: {k.proc.cmdline}"
        if k.act:
            w.log(f"reaped {what}: {w.system.kill_tree(k.proc.pid)}")
            continue
        key = str(k.proc.pid)
        noted[key] = state.get("noted", {}).get(key) or iso(now)
        if key not in state.get("noted", {}):
            w.log(f"would reap {what}, left alone: {k.why_not}")
    state["noted"] = noted


def _get(w: Watch, path: str) -> dict | None:
    try:
        return w.http.get(w.api(path))
    except HttpError as exc:
        w.log(f"GET {path} failed: {exc}")
        return None


def app_status(w: Watch) -> dict | None:
    """/api/status, tried a few times; None when it never answered."""
    for attempt in range(STATUS_TRIES):
        if attempt:
            w.sleep(STATUS_GAP_S)
        try:
            return w.http.get(w.api("/api/status"), timeout=STATUS_TIMEOUT_S)
        except HttpError:
            continue
    return None


def _server(state: dict, procs: Sequence[Proc]) -> Proc | None:
    """The app server status last reported, if its PID still holds that process."""
    p = next((p for p in procs if p.pid == state.get("server_pid")), None)
    if not p or "oracle" not in p.cmdline or "gui" not in p.cmdline:
        return None
    started = state.get("server_started_at")
    if started and p.created and abs(p.created - parse(started)) > SAME_START:
        return None
    return p


def restart_app(w: Watch, state: dict, procs: Sequence[Proc], was_down: bool) -> None:
    """Start the app from the live worktree when nothing holds its port. The server status last
    reported is killed first when its port is closed and it was down on the pass before too, or,
    port open (a hung event loop still accepts connections), after hung_passes passes down."""
    server = _server(state, procs)
    down_passes = state.get("app_restarts", 0) + 1
    if w.system.port_open(w.cfg.app_url):
        if not server or down_passes < w.cfg.hung_passes:
            w.log(f"app does not answer /api/status but its port answers: left running, not restarted"
                  f" (down {down_passes} pass(es))")
            return
        w.log(f"app server PID {server.pid} hung: no /api/status answer for {down_passes} passes though its"
              f" port accepts connections: killing it: {w.system.kill_tree(server.pid)}")
    elif server:
        if not was_down:
            w.log(f"app server PID {server.pid} does not answer and its port is closed: killed only if still"
                  f" down next pass")
            return
        w.log(f"app server PID {server.pid} still does not answer: killing it: {w.system.kill_tree(server.pid)}")
    pid = w.system.spawn(w.cfg.app_start, w.cfg.live, log=w.cfg.reports / "app-start.log")
    w.log(f"app down ({w.api('/api/status')} failed): started it from {w.cfg.live} (PID {pid})")


def ensure_ollama(w: Watch) -> None:
    try:
        w.http.get(f"{w.cfg.ollama_url}/api/tags")
    except HttpError as exc:
        pid = w.system.spawn(w.cfg.ollama_start, w.cfg.repo, log=w.cfg.reports / "ollama.log")
        w.log(f"Ollama down ({exc}): started {' '.join(w.cfg.ollama_start)} (PID {pid})")


def ensure_remote_control(w: Watch, procs: Sequence[Proc]) -> set[int]:
    """Every Remote Control session: the PID file's and any claude whose command line marks one
    (never reaped). With none, the PID file adopts the oldest marked one, else a new session is
    launched in a console."""
    cfg = w.cfg
    by_pid = {p.pid: p for p in procs}
    marked = sorted((p for p in procs if is_claude(p, cfg) and is_remote_control(p, cfg)),
                    key=lambda p: p.created or datetime.max.replace(tzinfo=w.now().tzinfo))
    pids = {p.pid for p in marked}
    pid = read_pid(cfg.rc_pid_file)
    if pid in by_pid and is_claude(by_pid[pid], cfg):
        return pids | {pid}
    if not cfg.rc_launch:
        return pids
    if marked:
        if not w.dry_run:
            write_pid(cfg.rc_pid_file, marked[0].pid)
        w.log(f"Remote Control session PID {marked[0].pid} adopted as the allowed one")
        return pids
    new = w.system.spawn(cfg.rc_launch, cfg.repo, console=True)
    if new is not None:
        write_pid(cfg.rc_pid_file, new)
    w.log(f"Remote Control session gone (PID file said {pid}): relaunched it (PID {new})")
    return {new} if new is not None else set()


def _run_process(lock: dict, procs: Sequence[Proc]) -> Proc | None:
    """The lock's supervisor, if its PID still holds it: a holiday_watch fix-run started no
    earlier than the lock."""
    p = next((p for p in procs if p.pid == lock["pid"]), None)
    if not p or "holiday_watch" not in p.cmdline or "fix-run" not in p.cmdline:
        return None
    if p.created and p.created < parse(lock["started_at"]) - RUN_START_SLACK:
        return None
    if p.created and p.created > parse(lock["started_at"]) + RUN_START_SLACK:
        return None
    return p


def _check_lock(w: Watch, procs: Sequence[Proc], now: datetime) -> dict | None:
    """The running fix run's lock, or None once it is gone, stale or overrun."""
    lock = read_lock(w.cfg.lock_file)
    if not lock:
        return None
    if _run_process(lock, procs) is None:
        w.log(f"fix-run lock of PID {lock['pid']} cleared: that fix run is gone")
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
