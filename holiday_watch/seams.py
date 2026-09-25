"""Every side effect the watch has: HTTP to the app and Ollama, the process list, starting and
killing processes, the log. Tests swap these for fakes; --dry-run swaps the harmful ones for log
lines."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Protocol

from holiday_watch import procs
from holiday_watch.config import Config
from holiday_watch.procs import NO_WINDOW, Proc

DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)


class HttpError(Exception):
    pass


class Http(Protocol):
    def get(self, url: str, timeout: float = 15.0) -> dict: ...
    def post(self, url: str, body: dict | None = None, timeout: float = 15.0) -> dict: ...


class UrllibHttp:
    def get(self, url: str, timeout: float = 15.0) -> dict:
        return self._call(urllib.request.Request(url), timeout)

    def post(self, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        data = json.dumps(body or {}).encode()
        return self._call(urllib.request.Request(url, data=data, method="POST",
                                                 headers={"Content-Type": "application/json"}), timeout)

    @staticmethod
    def _call(req: urllib.request.Request, timeout: float) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as answer:
                return json.loads(answer.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise HttpError(f"{req.full_url}: HTTP {exc.code} {exc.read()[:300]!r}") from None
        except (OSError, ValueError) as exc:
            raise HttpError(f"{req.full_url}: {exc}") from None


@dataclass(frozen=True)
class Ran:
    code: int | None
    out: str
    timed_out: bool = False


class System(Protocol):
    def processes(self) -> list[Proc]: ...
    def spawn(self, args: Sequence[str], cwd: Path, *, console: bool = False, log: Path | None = None) -> int | None:
        """Start a process that outlives the watch; hidden unless `console`. Its PID, or None."""
    def kill_tree(self, pid: int) -> str: ...
    def port_open(self, url: str) -> bool:
        """Something accepts connections on the URL's host and port."""
    def run(self, args: Sequence[str], stdin: str, timeout_s: float, cwd: Path,
            env: dict[str, str] | None = None) -> Ran:
        """Run hidden to the end, killing its whole tree past `timeout_s`."""


def resolved(args: Sequence[str]) -> list[str]:
    """PATH lookup that also finds .cmd shims, which CreateProcess alone does not."""
    return [shutil.which(args[0]) or args[0], *args[1:]]


class RealSystem:
    breakaway_refused = False
    def processes(self) -> list[Proc]:
        return procs.list_processes()

    def spawn(self, args: Sequence[str], cwd: Path, *, console: bool = False, log: Path | None = None) -> int | None:
        if console:
            # No handles given: the child uses its new console, which an interactive claude needs.
            return self._popen(args, cwd, NEW_CONSOLE, {})
        out = open(log, "ab") if log else subprocess.DEVNULL  # noqa: SIM115 (the child keeps it)
        try:
            return self._popen(args, cwd, DETACHED | NO_WINDOW,
                               {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out})
        finally:
            if log:
                out.close()

    def _popen(self, args: Sequence[str], cwd: Path, flags: int, handles: dict) -> int:
        try:
            return subprocess.Popen(resolved(args), cwd=cwd, creationflags=flags | BREAKAWAY, close_fds=True,
                                    **handles).pid
        except OSError:
            # A Task Scheduler job that forbids breakaway refuses the flag; the child then ends with the task.
            self.breakaway_refused = True
            return subprocess.Popen(resolved(args), cwd=cwd, creationflags=flags, close_fds=True, **handles).pid

    def kill_tree(self, pid: int) -> str:
        return procs.kill_tree(pid)

    def port_open(self, url: str) -> bool:
        u = urllib.parse.urlsplit(url)
        try:
            with socket.create_connection((u.hostname, u.port or 80), timeout=5):
                return True
        except OSError:
            return False

    def run(self, args: Sequence[str], stdin: str, timeout_s: float, cwd: Path,
            env: dict[str, str] | None = None) -> Ran:
        p = subprocess.Popen(resolved(args), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             env={**os.environ, **env} if env else None,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             creationflags=NO_WINDOW)
        try:
            out, _ = p.communicate(stdin, timeout=timeout_s)
            return Ran(p.returncode, out or "")
        except subprocess.TimeoutExpired:
            procs.kill_tree(p.pid)
            out, _ = p.communicate()
            return Ran(p.returncode, out or "", timed_out=True)


@dataclass
class DryRunSystem:
    """Reads the real process list; everything else is only said."""
    say: Callable[[str], None]
    real: System = field(default_factory=RealSystem)

    def processes(self) -> list[Proc]:
        return self.real.processes()

    def spawn(self, args: Sequence[str], cwd: Path, *, console: bool = False, log: Path | None = None) -> int | None:
        self.say(f"would start: {subprocess.list2cmdline(list(args))} (in {cwd})")
        return None

    def kill_tree(self, pid: int) -> str:
        self.say(f"would kill the process tree of PID {pid}")
        return "dry-run"

    def port_open(self, url: str) -> bool:
        return self.real.port_open(url)

    def run(self, args: Sequence[str], stdin: str, timeout_s: float, cwd: Path,
            env: dict[str, str] | None = None) -> Ran:
        self.say(f"would run: {subprocess.list2cmdline(list(args))} (in {cwd})")
        return Ran(None, "", timed_out=False)


@dataclass
class DryRunHttp:
    """Reads for real; every POST is only said."""
    real: Http
    say: Callable[[str], None]

    def get(self, url: str, timeout: float = 15.0) -> dict:
        return self.real.get(url, timeout)

    def post(self, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        self.say(f"would POST {url} {json.dumps(body) if body else ''}".rstrip())
        return {"dry_run": True}


@dataclass
class Watch:
    """What one command runs with."""
    cfg: Config
    http: Http
    system: System
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    dry_run: bool = False
    tz: tzinfo | None = None
    """None: the PC's own zone."""
    own_pid: int = 0
    config_path: Path | None = None
    echo: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep

    def log(self, message: str) -> None:
        """One line per action to watchdog.log, echoed."""
        line = f"{self.now().astimezone(self.tz).isoformat(timespec='seconds')} " \
               f"{'[dry-run] ' if self.dry_run else ''}{message}"
        self.cfg.reports.mkdir(parents=True, exist_ok=True)
        with self.cfg.log_file.open("a", encoding="utf-8") as f:
            f.write(line.replace("\n", " ") + "\n")
        self.echo(line)

    def api(self, path: str) -> str:
        return f"{self.cfg.app_url}{path}"


DRY_DIR = "dry-run"


def dry(w: Watch) -> Watch:
    """Make `w` a dry run: POSTs, starts, kills and claude runs are only logged, and its state,
    log, lock and fix-run files live in reports/dry-run/, which the morning report never reads.
    The Remote Control PID file is still read from the real place, never written."""
    w.dry_run = True
    w.cfg = replace(w.cfg, reports=w.cfg.reports / DRY_DIR)
    w.system = DryRunSystem(say=w.log, real=w.system)
    w.http = DryRunHttp(w.http, w.log)
    return w
