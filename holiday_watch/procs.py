"""The Windows process list (through PowerShell's CIM, no psutil) and tree walks over it."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from holiday_watch.timeutil import parse

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LIST_SCRIPT = (
    "Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{"
    "pid=$_.ProcessId; ppid=$_.ParentProcessId; name=$_.Name; cmd=$_.CommandLine;"
    "created=$(if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { $null });"
    "cpu=[double]($_.KernelModeTime + $_.UserModeTime) / 1e7 } } | ConvertTo-Json -Compress"
)


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    name: str
    cmdline: str
    created: datetime | None
    cpu_s: float
    """Kernel plus user CPU time since the process started."""


def parse_processes(text: str) -> list[Proc]:
    rows = json.loads(text) if text.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    return [Proc(int(r["pid"]), int(r.get("ppid") or 0), r.get("name") or "", r.get("cmd") or "",
                 _created(r.get("created")), float(r.get("cpu") or 0.0)) for r in rows]


def _created(text: str | None) -> datetime | None:
    # .NET's round-trip format carries 7 fractional digits; Python takes 6.
    return parse(re.sub(r"(\.\d{6})\d+", r"\1", text)) if text else None


def list_processes() -> list[Proc]:
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", LIST_SCRIPT],
                         capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW, check=True)
    return parse_processes(out.stdout)


def kill_tree(pid: int) -> str:
    """taskkill's own answer, for the log."""
    out = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
                         timeout=60, creationflags=NO_WINDOW)
    return (out.stdout + out.stderr).strip()


def descendants(procs: Iterable[Proc], roots: set[int]) -> set[int]:
    """The roots that exist and everything under them."""
    procs = list(procs)
    alive = {p.pid for p in procs}
    found = roots & alive
    grew = True
    while grew:
        more = {p.pid for p in procs if p.ppid in found and p.pid not in found and p.pid != p.ppid}
        found |= more
        grew = bool(more)
    return found


def ancestors(procs: Iterable[Proc], pid: int) -> set[int]:
    """`pid` and each parent up the chain that still exists."""
    parent = {p.pid: p.ppid for p in procs}
    chain = set()
    while pid in parent and pid not in chain:
        chain.add(pid)
        pid = parent[pid]
    return chain
