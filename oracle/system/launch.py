from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LaunchCommand", "server_command", "spawn_detached"]


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
    if os.name != "nt":
        return subprocess.Popen(cmd.argv, cwd=cmd.cwd, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid
    # Its own console, minimised without taking focus, so its log can still be read.
    info = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=7)  # SW_SHOWMINNOACTIVE
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Out of this process's job object, which may be killed with whatever started us.
        return subprocess.Popen(cmd.argv, cwd=cmd.cwd, startupinfo=info,
                                creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB).pid
    except OSError:
        # The job forbids breaking away; the process is still detached from our console.
        return subprocess.Popen(cmd.argv, cwd=cmd.cwd, startupinfo=info, creationflags=flags).pid
