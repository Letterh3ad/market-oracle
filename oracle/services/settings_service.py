"""Settings the GUI changes. Each is written to oracle.toml (so the CLI and later runs see
it) and applied to the running context."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path

from oracle.config import (
    DEFAULT_CONFIG_NAME, PEERS_RANGE, ConfigError, ScheduleConfig, check_peers, check_schedule_time, load_config,
    with_settings,
)
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery

__all__ = ["PEERS_RANGE", "set_peer_count", "set_schedule"]


def set_peer_count(ctx: AppContext, count: int) -> Path:
    """How many peers the value, quality and growth analysts compare against. Returns the
    file written; without one, it is made where `oracle` looks for it (the working folder).
    Every setter here does the same."""
    _check(check_peers, count)
    path = _save(ctx, "analysts", {"peers": count})
    ctx.config = replace(ctx.config, analysts=replace(ctx.config.analysts, peers=count))
    return path


def set_schedule(ctx: AppContext, enabled: bool, time: str) -> Path:
    """Turn automatic ticks on or off and set their local time; a running scheduler picks
    the change up on its next poll."""
    _check(check_schedule_time, time)
    path = _save(ctx, "schedule", {"enabled": enabled, "time": time})
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled, time))
    return path


def _check(check, value) -> None:
    try:
        check(value)
    except ConfigError as exc:
        raise InvalidQuery(str(exc)) from exc


def _save(ctx: AppContext, section: str, values: dict[str, object]) -> Path:
    path = ctx.config_source or Path.cwd() / DEFAULT_CONFIG_NAME
    _write_settings(path, section, values)
    ctx.config_source = path
    return path


def _write_settings(path: Path, section: str, values: dict[str, object]) -> None:
    """Change settings in one section in place, keeping the file's line endings. The new
    file replaces the old only once complete and loadable, so a crash or a bad edit never
    leaves the user with a truncated or unreadable oracle.toml."""
    raw = path.read_bytes() if path.is_file() else b""
    eol = "\r\n" if b"\r\n" in raw else "\n"
    try:
        updated = with_settings(raw.decode("utf-8").replace("\r\n", "\n"), section, values)
    except ConfigError as exc:
        keys = ", ".join(values)
        raise InvalidQuery(f"could not update {path} safely ({exc}); set [{section}] {keys} by hand") from exc
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".new", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(updated.replace("\n", eol).encode("utf-8"))
        try:
            load_config(staged, cwd=path.parent)
        except ConfigError as exc:
            raise InvalidQuery(f"{path} would not load after the change ({exc}); it was left as it was") from exc
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
