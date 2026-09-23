"""The running GUI server as a whole: restarting it, and saying which server and code this is."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from oracle.services.context import AppContext
from oracle.services.errors import CannotRestart
from oracle.system.launch import LaunchCommand, server_command, spawn_detached
from oracle.system.version import code_version

__all__ = ["AppControl", "server_launch"]

VERSION_TTL_S = 60.0
"""How long a read of the code on disk is trusted."""


def server_launch(ctx: AppContext) -> LaunchCommand:
    """How to start this GUI server again: same interpreter, config and folder."""
    return server_command(ctx.config_source, Path.cwd())


class AppControl:
    def __init__(self, command: LaunchCommand, spawn: Callable[[LaunchCommand], object] = spawn_detached,
                 read_version: Callable[[], str] = code_version, now: Callable[[], float] = time.monotonic) -> None:
        self.command = command
        self._spawn = spawn
        self._read_version, self._now = read_version, now
        self.version = read_version()
        """The code this server started with."""
        self._on_disk: tuple[str, float] | None = None
        self.pid = os.getpid()
        self.started_at = datetime.now(timezone.utc)
        self.on_exit: Callable[[], None] | None = None
        """Set by whatever serves the app: asks the server to stop once in-flight answers are sent."""

    def version_on_disk(self) -> str:
        """The code in the folder now; differs from `version` once new code has landed."""
        if self._on_disk is None or self._now() - self._on_disk[1] > VERSION_TTL_S:
            self._on_disk = self._read_version(), self._now()
        return self._on_disk[0]

    def restart(self) -> None:
        """Start a detached copy, then stop this server; the copy waits for the port."""
        if self.on_exit is None:
            raise CannotRestart("this server cannot stop itself (it was not started by `oracle gui`)")
        try:
            self._spawn(self.command)
        except OSError as exc:
            raise CannotRestart(f"could not start a new copy: {exc}") from exc
        self.on_exit()
