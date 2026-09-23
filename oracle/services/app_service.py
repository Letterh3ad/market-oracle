"""The running GUI server as a whole: restarting it, and saying which server this is."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from oracle.services.context import AppContext
from oracle.services.errors import CannotRestart
from oracle.system.launch import LaunchCommand, server_command, spawn_detached

__all__ = ["AppControl", "server_launch"]


def server_launch(ctx: AppContext) -> LaunchCommand:
    """How to start this GUI server again: same interpreter, config and folder."""
    return server_command(ctx.config_source, Path.cwd())


class AppControl:
    def __init__(self, command: LaunchCommand, spawn: Callable[[LaunchCommand], object] = spawn_detached) -> None:
        self.command = command
        self._spawn = spawn
        self.pid = os.getpid()
        self.started_at = datetime.now(timezone.utc)
        self.on_exit: Callable[[], None] | None = None
        """Set by whatever serves the app: asks the server to stop once in-flight answers are sent."""

    def restart(self) -> None:
        """Start a detached copy, then stop this server; the copy waits for the port."""
        if self.on_exit is None:
            raise CannotRestart("this server cannot stop itself (it was not started by `oracle gui`)")
        try:
            self._spawn(self.command)
        except OSError as exc:
            raise CannotRestart(f"could not start a new copy: {exc}") from exc
        self.on_exit()
