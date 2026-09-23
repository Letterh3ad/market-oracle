"""The running GUI server as a whole: restarting it, saying which server and code this is, and
starting it when Windows starts."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from oracle.services.context import AppContext
from oracle.services.errors import CannotRestart, ServiceError
from oracle.system.launch import LaunchCommand, server_command, spawn_detached
from oracle.system.shortcuts import shortcut_folders, startup_folder, write_shortcut
from oracle.system.version import CODE_ROOT, code_version

__all__ = ["AppControl", "Autostart", "autostart_for", "install_shortcuts", "server_launch"]

APP_NAME = "Market Oracle"

VERSION_TTL_S = 60.0
"""How long a read of the code on disk is trusted."""


def server_launch(ctx: AppContext) -> LaunchCommand:
    """How to start this GUI server again: same interpreter and config, in the folder its
    code was loaded from (the live worktree), whatever folder this one was started in.
    Without a config file the settings, database included, come from the working folder,
    so that one is kept."""
    return server_command(ctx.config_source, CODE_ROOT if ctx.config_source else Path.cwd())


class Autostart:
    """A launcher in the Startup folder; its presence is the setting, so deleting it by hand
    turns start-at-login off."""

    def __init__(self, folder: Path | None, command: LaunchCommand,
                 write: Callable[[Path, LaunchCommand, str], None] | None = None) -> None:
        self._folder = folder
        self._command = command
        self._write = write

    @property
    def path(self) -> Path | None:
        return self._folder / f"{APP_NAME}.lnk" if self._folder else None

    def enabled(self) -> bool:
        return self.path is not None and self.path.exists()

    def set(self, on: bool) -> None:
        if self.path is None:
            raise ServiceError("starting with the computer is only set up for Windows so far")
        if on:
            _write_links([self.path], self._command, self._write)
        else:
            self.path.unlink(missing_ok=True)


def _write_links(links: list[Path], command: LaunchCommand,
                 write: Callable[[Path, LaunchCommand, str], None] | None) -> None:
    try:
        for link in links:
            (write or write_shortcut)(link, command, APP_NAME)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServiceError(f"could not write {link}: {exc}") from exc


def install_shortcuts(command: LaunchCommand, folders: list[Path] | None = None,
                      write: Callable[[Path, LaunchCommand, str], None] | None = None) -> list[Path]:
    """Market Oracle on the desktop and in the Start menu, starting the GUI as the
    start-at-login launcher does. Returns the shortcuts written."""
    folders = shortcut_folders() if folders is None else folders
    if not folders:
        raise ServiceError("shortcuts are only set up for Windows so far")
    links = [f / f"{APP_NAME}.lnk" for f in folders]
    _write_links(links, command, write)
    return links


def autostart_for(ctx: AppContext) -> Autostart:
    return Autostart(startup_folder(), server_launch(ctx))


class AppControl:
    def __init__(self, command: LaunchCommand, spawn: Callable[[LaunchCommand], object] = spawn_detached,
                 read_version: Callable[[], str] = code_version, now: Callable[[], float] = time.monotonic,
                 autostart: Autostart | None = None) -> None:
        self.command = command
        self.autostart = autostart or Autostart(startup_folder(), command)
        self._spawn = spawn
        self._read_version, self._now = read_version, now
        self.version = read_version()
        """The code this server started with."""
        self._on_disk: tuple[str, float] | None = None
        self.pid = os.getpid()
        self.started_at = datetime.now(timezone.utc)
        self.on_exit: Callable[[], None] | None = None
        """Set by whatever serves the app: asks the server to stop once in-flight answers are sent."""
        self._stopping = False

    def version_on_disk(self) -> str:
        """The code in the folder now; differs from `version` once new code has landed."""
        if self._on_disk is None or self._now() - self._on_disk[1] > VERSION_TTL_S:
            self._on_disk = self._read_version(), self._now()
        return self._on_disk[0]

    def restart(self) -> None:
        """Start a detached copy, then stop this server; the copy waits for the port."""
        stop = self._exit_hook()
        try:
            self._spawn(self.command)
        except OSError as exc:
            raise CannotRestart(f"could not start a new copy: {exc}") from exc
        self._stopping = True
        stop()

    def shut_down(self) -> None:
        """Stop this server for good; the desktop shortcut starts it again."""
        stop = self._exit_hook()
        self._stopping = True
        stop()

    def _exit_hook(self) -> Callable[[], None]:
        # A double click, or the CLI and the page together, must not start two copies.
        if self._stopping:
            raise CannotRestart("Oracle is already restarting or shutting down")
        if self.on_exit is None:
            raise CannotRestart("this server cannot stop itself (it was not started by `oracle gui`)")
        return self.on_exit
