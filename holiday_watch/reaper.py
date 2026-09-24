"""Which claude processes to kill: pure decisions over a process list and the CPU seen before."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from holiday_watch.config import Config
from holiday_watch.procs import Proc, ancestors, descendants
from holiday_watch.timeutil import iso, parse


@dataclass(frozen=True)
class Kill:
    proc: Proc
    reason: str
    age_s: float | None


def is_claude(p: Proc, patterns: Sequence[str]) -> bool:
    text = f"{p.name} {p.cmdline}".lower()
    return any(pat.lower() in text for pat in patterns)


def track_cpu(track: dict, procs: Sequence[Proc], now: datetime, cfg: Config) -> dict:
    """Per claude PID, a CPU baseline and when it was taken; kept while the process stays under
    idle_cpu_s above it, retaken when it works or the PID is a new process."""
    out = {}
    for p in procs:
        if not is_claude(p, cfg.reaper_match):
            continue
        created = iso(p.created) if p.created else None
        seen = track.get(str(p.pid))
        if seen and seen.get("created") == created and p.cpu_s - seen["cpu"] < cfg.idle_cpu_s:
            out[str(p.pid)] = seen
        else:
            out[str(p.pid)] = {"created": created, "cpu": p.cpu_s, "since": iso(now)}
    return out


def protected_pids(procs: Sequence[Proc], now: datetime, cfg: Config, *, rc_pid: int | None,
                   lock: dict | None, own_pid: int) -> set[int]:
    """The Remote Control session's tree, a fix run's tree within its time limit, and the chain
    that started this watchdog."""
    roots = {rc_pid} if rc_pid else set()
    if lock and now - parse(lock["started_at"]) < timedelta(minutes=cfg.fix_timeout_min):
        roots.add(int(lock["pid"]))
    return descendants(procs, roots) | ancestors(procs, own_pid)


def choose_kills(procs: Sequence[Proc], now: datetime, track: dict, protected: set[int],
                 cfg: Config) -> list[Kill]:
    kills: list[Kill] = []
    for p in procs:
        if p.pid in protected or not is_claude(p, cfg.reaper_match):
            continue
        age = (now - p.created).total_seconds() if p.created else None
        seen = track.get(str(p.pid))
        if age is not None and age >= cfg.max_age_h * 3600:
            kills.append(Kill(p, f"past its {cfg.max_age_h:g} h time limit", age))
        elif seen and p.cpu_s - seen["cpu"] < cfg.idle_cpu_s:
            idle = now - parse(seen["since"])
            if idle >= timedelta(minutes=cfg.idle_min):
                kills.append(Kill(p, f"idle for {idle.total_seconds() / 60:.0f} min", age))
    doomed = {k.proc.pid for k in kills}
    return [k for k in kills if not (ancestors(procs, k.proc.ppid) & doomed)]
