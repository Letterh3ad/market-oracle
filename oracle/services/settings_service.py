"""Settings the GUI changes. Each is written to oracle.toml (so the CLI and later runs see
it) and applied to the running context."""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from tomlkit.exceptions import TOMLKitError

from oracle.config import (
    BLANK_OFF, DEFAULT_CONFIG_NAME, DEFAULT_PROFILE, LLM_PROFILE_KEYS, EXAMPLE_PATH, SLOTS_TABLE, PEERS_RANGE, AutomationConfig, ConfigError, ScheduleConfig, Setting,
    check_ai_account, check_peers, check_schedule, check_schedule_time, example_meanings, key_env_var, load_config,
    setting_list, with_settings,
)
from oracle.services.context import AppContext
from oracle.services.errors import ConfigInUse, InvalidQuery

log = logging.getLogger(__name__)

__all__ = [
    "LIVE_SECTIONS", "PEERS_RANGE", "edit_config", "reload_live_settings", "set_ai_account", "set_ai_research", "set_automation", "set_regime_account", "set_regime_ai_account",
    "set_peer_count", "set_schedule", "set_setting", "set_tick_warning", "settings_form",
]

LIVE_SECTIONS = ("schedule", "automation", "ai_account", "regime_account", "regime_ai_account", "analysts", "universe", "discovery", "models")
"""Sections a running app takes from oracle.toml as soon as it changes; the rest wait for a restart.
A nested table ([models.slots]) is live with its section."""
LIVE_FIELDS = (*LIVE_SECTIONS, "model_slots", "allow_claude_code")
"""The Config fields those sections load into."""


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
    schedule = _check_schedule_write(ctx, {"enabled": enabled, "time": time})
    path = _save(ctx, "schedule", {"enabled": enabled, "time": time})
    ctx.config = replace(ctx.config, schedule=schedule)
    return path


def set_tick_warning(ctx: AppContext, warn_minutes: int, research_when_idle: bool, idle_minutes: int) -> Path:
    """How long before a scheduled tick the GUI warns (0 off), and whether a tick researches
    only once the PC has been idle `idle_minutes`."""
    values = {"warn_minutes": warn_minutes, "research_when_idle": research_when_idle, "idle_minutes": idle_minutes}
    schedule = _check_schedule_write(ctx, values)
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


def set_regime_account(ctx: AppContext, enabled: bool) -> Path:
    """Turn the regime paper account on or off; off, its step decides nothing."""
    path = _save(ctx, "regime_account", {"enabled": enabled})
    ctx.config = replace(ctx.config, regime_account=replace(ctx.config.regime_account, enabled=enabled))
    return path


def set_regime_ai_account(ctx: AppContext, enabled: bool | None = None, model: str | None = None) -> Path:
    """The regime-ai account's switch and the model profile that tilts it ("" the research
    slot's); None leaves a value as it is."""
    values = {k: v for k, v in {"enabled": enabled, "model": model}.items() if v is not None}
    path = _save(ctx, "regime_ai_account", values)
    ctx.config = replace(ctx.config, regime_ai_account=replace(ctx.config.regime_ai_account, **values))
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
    if section == "schedule":
        _check_schedule_write(ctx, {key: value})
    _save(ctx, section, {key: value})
    if _live(section):
        reload_live_settings(ctx)
    return next(r for r in settings_form(ctx) if (r["section"], r["key"]) == (section, key))


def _row(setting: Setting, raw: dict, meanings: dict) -> dict:
    table = raw
    for part in filter(None, setting.section.split(".")):
        table = table.get(part, {})
    row = {"section": setting.section, "key": setting.key, "kind": setting.kind, "default": setting.default,
           "meaning": setting.meaning_in(meanings) or "", "live": _live(setting.section),
           "secret": setting.secret}
    if setting.secret:
        env = key_env_var(setting.key)
        return {**row, "set": setting.key in table, "env": env, "from_env": bool(os.environ.get(env))}
    if (setting.section, setting.key) in BLANK_OFF:
        row["blank_off"] = True
    if setting.section == f"models.{SLOTS_TABLE}":
        row["choices"] = [DEFAULT_PROFILE, *(n for n in raw.get("models", {}) if n not in (DEFAULT_PROFILE, SLOTS_TABLE))]
    if setting.section == "llm" and setting.key in LLM_PROFILE_KEYS and DEFAULT_PROFILE in raw.get("models", {}):
        row["ignored"] = "ignored: [models.default] replaces it (Settings > Models)"
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
        # Lenient: a slot naming a gone profile is dropped with a warning rather than freezing
        # every live setting (the automation switch included) at its old value.
        loaded = load_config(ctx.config_source)[0]
    except (ConfigError, tomllib.TOMLDecodeError, OSError) as exc:
        log.warning("oracle.toml no longer loads (%s); keeping the current live settings", exc)
        return
    ctx.config = replace(ctx.config, **{name: getattr(loaded, name) for name in LIVE_FIELDS})


def _live(section: str) -> bool:
    return section.split(".")[0] in LIVE_SECTIONS


def _check_schedule_write(ctx: AppContext, values: dict[str, object]) -> ScheduleConfig:
    """The schedule after `values` (None: the default), refusing a midday time equal to the tick's."""
    defaults = asdict(ScheduleConfig())
    merged = {**asdict(ctx.config.schedule), **{k: defaults[k] if v is None else v for k, v in values.items()}}
    return _check(lambda raw: check_schedule(raw, strict=True), merged)


def _check(check, value):
    try:
        return check(value)
    except ConfigError as exc:
        raise InvalidQuery(str(exc)) from exc


def _config_path(ctx: AppContext) -> Path:
    return ctx.config_source or Path.cwd() / DEFAULT_CONFIG_NAME


def _save(ctx: AppContext, section: str, values: dict[str, object]) -> Path:
    return edit_config(ctx, lambda text: with_settings(text, section, values), f"set [{section}] {', '.join(values)}")


def edit_config(ctx: AppContext, edit: Callable[[str], str], by_hand: str) -> Path:
    """Apply `edit` to oracle.toml's text and write it back safely; `by_hand` says what to
    do instead when the edit cannot be made. Returns the file written."""
    path = _config_path(ctx)
    _write_settings(path, edit, by_hand)
    ctx.config_source = path
    return path


def _write_settings(path: Path, edit: Callable[[str], str], by_hand: str) -> None:
    """Change settings in place, keeping the file's line endings. The new file replaces the
    old only once complete and loadable, so a crash or a bad edit never leaves the user with
    a truncated or unreadable oracle.toml."""
    raw = path.read_bytes() if path.is_file() else b""
    eol = "\r\n" if b"\r\n" in raw else "\n"
    try:
        updated = edit(raw.decode("utf-8").replace("\r\n", "\n"))
    # tomlkit raises ValueError for shapes it cannot extend, such as a table inside an inline table.
    except (ConfigError, TOMLKitError, ValueError) as exc:
        raise InvalidQuery(f"could not update {path} safely ({exc}); {by_hand} by hand") from exc
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".new", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(updated.replace("\n", eol).encode("utf-8"))
        try:
            load_config(staged, cwd=path.parent, strict=True)
        except ConfigError as exc:
            raise InvalidQuery(f"{path} would not load after the change ({exc}); it was left as it was") from exc
        try:
            os.replace(staged, path)
        except OSError as exc:
            # Windows refuses to replace a file another program (an editor, Excel) has open.
            raise ConfigInUse(f"{path.name} is open in another program: close it and try again") from exc
    finally:
        staged.unlink(missing_ok=True)
