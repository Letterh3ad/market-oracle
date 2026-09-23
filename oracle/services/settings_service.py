"""Settings the GUI changes. Each is written to oracle.toml (so the CLI and later runs see
it) and applied to the running context."""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from dataclasses import replace
from pathlib import Path

from oracle.config import (
    DEFAULT_CONFIG_NAME, PEERS_RANGE, AutomationConfig, ConfigError, check_ai_account, check_peers, check_schedule,
    check_schedule_time, load_config, with_settings,
)
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery

log = logging.getLogger(__name__)

__all__ = [
    "PEERS_RANGE", "reload_live_settings", "set_ai_account", "set_ai_research", "set_automation", "set_peer_count",
    "set_schedule", "set_tick_warning",
]


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
    ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, enabled=enabled, time=time))
    return path


def set_tick_warning(ctx: AppContext, warn_minutes: int, research_when_idle: bool, idle_minutes: int) -> Path:
    """How long before a scheduled tick the GUI warns (0 off), and whether a tick researches
    only once the PC has been idle `idle_minutes`."""
    values = {"warn_minutes": warn_minutes, "research_when_idle": research_when_idle, "idle_minutes": idle_minutes}
    s = ctx.config.schedule
    schedule = _check(check_schedule, {"enabled": s.enabled, "time": s.time, **values})
    path = _save(ctx, "schedule", values)
    ctx.config = replace(ctx.config, schedule=schedule)
    return path


def set_automation(ctx: AppContext, enabled: bool) -> Path:
    """The master switch for work that starts by itself; a running scheduler sees it on its
    next poll."""
    path = _save(ctx, "automation", {"enabled": enabled})
    ctx.config = replace(ctx.config, automation=AutomationConfig(enabled))
    return path


def set_ai_account(ctx: AppContext, enabled: bool) -> Path:
    """Turn the AI paper account on or off; off, its step does nothing."""
    path = _save(ctx, "ai_account", {"enabled": enabled})
    ctx.config = replace(ctx.config, ai_account=replace(ctx.config.ai_account, enabled=enabled))
    return path


def set_ai_research(ctx: AppContext, max_age_days: int, move_threshold: float, budget_min: int) -> Path:
    """How ticks keep the AI account's research fresh: a report older than `max_age_days`
    (also the oldest verdict it trades) or a price move past `move_threshold` since it is
    researched again, for up to `budget_min` minutes a tick."""
    values = {"max_verdict_age_days": max_age_days, "move_threshold": move_threshold,
              "research_budget_min": budget_min}
    ai = _check(check_ai_account, {"enabled": ctx.config.ai_account.enabled, **values})
    path = _save(ctx, "ai_account", values)
    ctx.config = replace(ctx.config, ai_account=ai)
    return path


def reload_live_settings(ctx: AppContext) -> None:
    """Take `[schedule]`, `[automation]` and `[ai_account]` from oracle.toml as they are now, so the CLI in
    another process reaches a running GUI. A file that no longer loads keeps the current
    settings."""
    if ctx.config_source is None:
        return
    try:
        loaded = load_config(ctx.config_source)[0]
    except (ConfigError, tomllib.TOMLDecodeError, OSError) as exc:
        log.warning("oracle.toml no longer loads (%s); keeping the current live settings", exc)
        return
    ctx.config = replace(ctx.config, schedule=loaded.schedule, automation=loaded.automation,
                         ai_account=loaded.ai_account)


def _check(check, value):
    try:
        return check(value)
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
