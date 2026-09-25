"""Which claude processes to kill: pure decisions over a process list and the CPU seen before."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from holiday_watch.config import Config
from holiday_watch.procs import Proc, ancestors, descendants
from holiday_watch.timeutil import iso, parse

HEADLESS = ("-p", "--print")


@dataclass(frozen=True)
class Kill:
    proc: Proc
    reason: str
    age_s: float | None
    act: bool = True
    why_not: str = ""
    """Why an idle or old process is only noted, when `act` is False."""


def words(cmdline: str) -> list[str]:
    return [w.strip('"') for w in re.findall(r'"[^"]*"|\S+', cmdline)]


def is_claude(p: Proc, cfg: Config) -> bool:
    """Its executable is claude, or node running Claude Code; never a spared one (the desktop app,
    Claude Code's daemon)."""
    text = f"{p.name} {p.cmdline}".lower()
    if any(s.lower() in text for s in cfg.reaper_spare):
        return False
    name = p.name.lower()
    if name in (n.lower() for n in cfg.reaper_names):
        return True
    return name == "node.exe" and any(s.lower() in p.cmdline.lower() for s in cfg.reaper_node_scripts)


def is_headless(p: Proc) -> bool:
    return any(w in HEADLESS or w.startswith("--print=") for w in words(p.cmdline))


def is_remote_control(p: Proc, cfg: Config) -> bool:
    return any(w in cfg.rc_markers or w.split("=", 1)[0] in cfg.rc_markers for w in words(p.cmdline))


def track_cpu(track: dict, procs: Sequence[Proc], now: datetime, cfg: Config) -> dict:
    """Per claude PID, a CPU baseline and when it was taken; kept while the process stays under
    idle_cpu_s above it, retaken when it works or the PID is a new process."""
    out = {}
    for p in procs:
        if not is_claude(p, cfg):
            continue
        created = iso(p.created) if p.created else None
        seen = track.get(str(p.pid))
        if seen and seen.get("created") == created and p.cpu_s - seen["cpu"] < cfg.idle_cpu_s:
            out[str(p.pid)] = seen
        else:
            out[str(p.pid)] = {"created": created, "cpu": p.cpu_s, "since": iso(now)}
    return out


def protected_pids(procs: Sequence[Proc], now: datetime, cfg: Config, *, rc_pids: set[int], lock: dict | None,
                   own_pid: int, server_pid: int | None) -> set[int]:
    """The Remote Control sessions' trees, a fix run's tree within its time limit, the app server's
    tree (its claude children have their own timeouts), and the chain that started this watchdog."""
    roots = set(rc_pids)
    if lock and now - parse(lock["started_at"]) < timedelta(minutes=cfg.fix_timeout_min):
        roots.add(int(lock["pid"]))
    if server_pid:
        roots.add(int(server_pid))
    return descendants(procs, roots) | ancestors(procs, own_pid)


def choose_kills(procs: Sequence[Proc], now: datetime, track: dict, protected: set[int],
                 cfg: Config) -> list[Kill]:
    """Idle or old claude processes. `act` only for a headless one (any, with reap_interactive)
    whose tree holds nothing protected: the kill takes the whole tree."""
    found: list[Kill] = []
    for p in procs:
        if p.pid in protected or not is_claude(p, cfg) or is_remote_control(p, cfg):
            continue
        age = (now - p.created).total_seconds() if p.created else None
        seen = track.get(str(p.pid))
        reason = None
        if age is not None and age >= cfg.max_age_h * 3600:
            reason = f"past its {cfg.max_age_h:g} h time limit"
        elif seen and p.cpu_s - seen["cpu"] < cfg.idle_cpu_s:
            idle = now - parse(seen["since"])
            if idle >= timedelta(minutes=cfg.idle_min):
                reason = f"idle for {idle.total_seconds() / 60:.0f} min"
        if reason is None:
            continue
        why_not = ""
        if descendants(procs, {p.pid}) & protected:
            why_not = "its tree holds a protected process"
        elif not (cfg.reap_interactive or is_headless(p)):
            why_not = "interactive, and reap_interactive is off"
        found.append(Kill(p, reason, age, act=not why_not, why_not=why_not))
    doomed = {k.proc.pid for k in found if k.act}
    return [k for k in found if not (k.act and ancestors(procs, k.proc.ppid) & doomed)]
