"""Settings the GUI changes. Each is written to oracle.toml (so the CLI and later runs see
it) and applied to the running context."""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import replace
from pathlib import Path

from oracle.config import DEFAULT_CONFIG_NAME, PEERS_RANGE, ConfigError, check_peers, load_config, with_setting
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery

__all__ = ["PEERS_RANGE", "set_peer_count"]


def set_peer_count(ctx: AppContext, count: int) -> Path:
    """How many peers the value, quality and growth analysts compare against. Returns the
    file written; without one, it is made where `oracle` looks for it (the working folder)."""
    try:
        check_peers(count)
    except ConfigError as exc:
        raise InvalidQuery(str(exc)) from exc
    path = ctx.config_source or Path.cwd() / DEFAULT_CONFIG_NAME
    _write_setting(path, "analysts", "peers", str(count))
    ctx.config = replace(ctx.config, analysts=replace(ctx.config.analysts, peers=count))
    ctx.config_source = path
    return path


def _write_setting(path: Path, section: str, key: str, literal: str) -> None:
    """Change one setting in place, keeping the file's line endings. The new file replaces
    the old only once complete and loadable, so a crash or a bad edit never leaves the user
    with a truncated or unreadable oracle.toml."""
    raw = path.read_bytes() if path.is_file() else b""
    eol = "\r\n" if b"\r\n" in raw else "\n"
    updated = with_setting(raw.decode("utf-8").replace("\r\n", "\n"), section, key, literal)
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidQuery(f"could not update {path} safely ({exc}); set [{section}] {key} by hand") from exc
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
