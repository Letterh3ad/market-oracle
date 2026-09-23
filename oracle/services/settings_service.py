"""Settings the GUI changes. Each is written to oracle.toml (so the CLI and later runs see
it) and applied to the running context."""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from oracle.config import DEFAULT_CONFIG_NAME, PEERS_RANGE, AnalystsConfig, ConfigError, load_config, with_setting
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery

__all__ = ["PEERS_RANGE", "set_peer_count"]


def set_peer_count(ctx: AppContext, count: int) -> Path:
    """How many peers the value, quality and growth analysts compare against. Returns the
    file written; without one, it is made where `oracle` looks for it (the working folder)."""
    low, high = PEERS_RANGE
    if isinstance(count, bool) or not low <= count <= high:
        raise InvalidQuery(f"peers must be a whole number from {low} to {high}, not {count}")
    path = ctx.config_source or Path.cwd() / DEFAULT_CONFIG_NAME
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = with_setting(text, "analysts", "peers", str(count))
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        # Never leave a file oracle cannot read; the user's file is left untouched.
        raise InvalidQuery(f"could not update {path} safely ({exc}); set [analysts] peers by hand") from exc
    path.write_text(updated, encoding="utf-8")
    try:
        load_config(path)
    except ConfigError as exc:
        path.write_text(text, encoding="utf-8")
        raise InvalidQuery(f"{path} would not load after the change ({exc}); it was put back") from exc
    ctx.config = replace(ctx.config, analysts=AnalystsConfig(peers=count))
    ctx.config_source = path
    return path
