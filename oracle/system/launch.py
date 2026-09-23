from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LaunchCommand", "server_command", "spawn_detached"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaunchCommand:
    argv: tuple[str, ...]
    cwd: Path


def server_command(config_source: Path | None, cwd: Path) -> LaunchCommand:
    """The GUI server with this interpreter, config and folder, spelled out rather than
    copied from `sys.argv`, so a relative path in how this one was started cannot drift."""
    config = ("--config", str(config_source.resolve())) if config_source else ()
    return LaunchCommand((sys.executable, "-m", "oracle", *config, "gui"), cwd)


def spawn_detached(cmd: LaunchCommand) -> int:
    """Start `cmd` so it outlives this process and whatever started it; returns its pid."""
    proc = _popen(cmd)
    # Never waited on, by design; this stops Python warning about a child left running.
    proc.returncode = 0
    return proc.pid


def _popen(cmd: LaunchCommand) -> subprocess.Popen:
    if os.name != "nt":
        return subprocess.Popen(cmd.argv, cwd=cmd.cwd, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Its own console, minimised without taking focus, so its log can still be read.
    info = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=7)  # SW_SHOWMINNOACTIVE
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Out of this process's job object, which may be killed with whatever started us.
        return subprocess.Popen(cmd.argv, cwd=cmd.cwd, startupinfo=info,
                                creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        log.warning("could not start %s outside this process's job; it may stop when whatever started "
                    "this server exits", cmd.argv[0])
        return subprocess.Popen(cmd.argv, cwd=cmd.cwd, startupinfo=info, creationflags=flags)
