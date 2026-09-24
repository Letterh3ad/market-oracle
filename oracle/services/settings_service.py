"""Settings the GUI changes. Each is written to oracle.toml (so the CLI and later runs see
it) and applied to the running context."""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from dataclasses import asdict, replace
from pathlib import Path

from oracle.config import (
    BLANK_OFF, DEFAULT_CONFIG_NAME, EXAMPLE_PATH, PEERS_RANGE, AutomationConfig, ConfigError, Setting, check_ai_account,
    check_peers, check_schedule, check_schedule_time, example_meanings, key_env_var, load_config, setting_list,
    with_settings,
)
from oracle.services.context import AppContext
from oracle.services.errors import ConfigInUse, InvalidQuery

log = logging.getLogger(__name__)

__all__ = [
    "LIVE_SECTIONS", "PEERS_RANGE", "reload_live_settings", "set_ai_account", "set_ai_research", "set_automation",
    "set_peer_count", "set_schedule", "set_setting", "set_tick_warning", "settings_form",
]

LIVE_SECTIONS = ("schedule", "automation", "ai_account", "analysts", "universe", "discovery")
"""Sections a running app takes from oracle.toml as soon as it changes; the rest wait for a restart."""


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
    schedule = _check(check_schedule, {**asdict(s), **values})
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


def settings_form(ctx: AppContext) -> list[dict]:
    """Every oracle.toml key: its value in the file (else the default), default, meaning
    from oracle.toml.example, and whether it applies live. Secrets say only whether they
    are set, and whether the environment sets them (which wins over the file)."""
    raw = _read_raw(_config_path(ctx))
    meanings = example_meanings(EXAMPLE_PATH.read_text(encoding="utf-8")) if EXAMPLE_PATH.is_file() else {}
    return [_row(s, raw, meanings) for s in setting_list(tuple(raw.get("sources", {})))]


def set_setting(ctx: AppContext, section: str, key: str, value: object) -> dict:
    """Save one key (None removes it, so the default applies) once the whole file still
    loads, refusing with the loader's message otherwise. Live sections apply at once."""
    raw = _read_raw(_config_path(ctx))
    if not any((s.section, s.key) == (section, key) for s in setting_list(tuple(raw.get("sources", {})))):
        raise InvalidQuery(f"no setting [{section}] {key}")
    _save(ctx, section, {key: value})
    if section in LIVE_SECTIONS:
        reload_live_settings(ctx)
    return next(r for r in settings_form(ctx) if (r["section"], r["key"]) == (section, key))


def _row(setting: Setting, raw: dict, meanings: dict) -> dict:
    table = raw
    for part in filter(None, setting.section.split(".")):
        table = table.get(part, {})
    row = {"section": setting.section, "key": setting.key, "kind": setting.kind, "default": setting.default,
           "meaning": setting.meaning_in(meanings) or "", "live": setting.section in LIVE_SECTIONS,
           "secret": setting.secret}
    if setting.secret:
        env = key_env_var(setting.key)
        return {**row, "set": setting.key in table, "env": env, "from_env": bool(os.environ.get(env))}
    if (setting.section, setting.key) in BLANK_OFF:
        row["blank_off"] = True
    return {**row, "value": table.get(setting.key, setting.default)}


def _read_raw(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except tomllib.TOMLDecodeError as exc:
        raise InvalidQuery(f"{path} is not valid TOML ({exc}); fix it by hand") from exc


def reload_live_settings(ctx: AppContext) -> None:
    """Take the LIVE_SECTIONS from oracle.toml as they are now, so the CLI in another
    process reaches a running GUI. A file that no longer loads keeps the current settings."""
    if ctx.config_source is None:
        return
    try:
        loaded = load_config(ctx.config_source)[0]
    except (ConfigError, tomllib.TOMLDecodeError, OSError) as exc:
        log.warning("oracle.toml no longer loads (%s); keeping the current live settings", exc)
        return
    ctx.config = replace(ctx.config, **{section: getattr(loaded, section) for section in LIVE_SECTIONS})


def _check(check, value):
    try:
        return check(value)
    except ConfigError as exc:
        raise InvalidQuery(str(exc)) from exc


def _config_path(ctx: AppContext) -> Path:
    return ctx.config_source or Path.cwd() / DEFAULT_CONFIG_NAME


def _save(ctx: AppContext, section: str, values: dict[str, object]) -> Path:
    path = _config_path(ctx)
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
        try:
            os.replace(staged, path)
        except OSError as exc:
            # Windows refuses to replace a file another program (an editor, Excel) has open.
            raise ConfigInUse(f"{path.name} is open in another program: close it and try again") from exc
    finally:
        staged.unlink(missing_ok=True)
