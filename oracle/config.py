from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

DEFAULT_CONFIG_NAME = "oracle.toml"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    rate_per_second: float
    cooldown_s: float = 900.0
    max_retries: int = 3


# Conservative on purpose: most of these are unofficial or unmetered-but-watched, and
# an IP block costs far more than a slow tick. Reasons in DECISIONS.md.
DEFAULT_SOURCES: dict[str, SourceConfig] = {
    "yfinance": SourceConfig(rate_per_second=0.5),
    "stooq": SourceConfig(rate_per_second=0.5),
    "gdelt": SourceConfig(rate_per_second=0.2),
    "google_news": SourceConfig(rate_per_second=0.5),
    "ddgs": SourceConfig(rate_per_second=0.2),
    "sec_edgar": SourceConfig(rate_per_second=5.0),
    "financedatabase": SourceConfig(rate_per_second=0.5),
}


@dataclass(frozen=True)
class Config:
    db_path: Path = Path("oracle.db")
    base_currency: str = "GBP"
    sources: dict[str, SourceConfig] = field(default_factory=lambda: dict(DEFAULT_SOURCES))


def load_config(path: Path | None, cwd: Path | None = None) -> tuple[Config, Path | None]:
    """Return the config and the file it came from, or None when defaults were used."""
    cwd = cwd or Path.cwd()
    if path is None:
        path = cwd / DEFAULT_CONFIG_NAME
        if not path.is_file():
            return _resolve(Config(), cwd), None
    elif not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return _resolve(_apply(raw), path.parent), path


def _reject_unknown(raw: dict, cls: type, where: str) -> None:
    unknown = sorted(set(raw) - {f.name for f in fields(cls)})
    if unknown:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(unknown)}")


def _apply(raw: dict) -> Config:
    _reject_unknown(raw, Config, "oracle.toml")
    values = {}
    for name, value in raw.items():
        if name == "sources":
            values[name] = _sources(value)
            continue
        expected = Path if name == "db_path" else type(getattr(Config, name))
        if expected is Path:
            if not isinstance(value, str):
                raise ConfigError(f"{name} must be a string path, got {value!r}")
            value = Path(value)
        elif not isinstance(value, expected):
            raise ConfigError(f"{name} must be {expected.__name__}, got {value!r}")
        values[name] = value
    config = replace(Config(), **values)
    if not re.fullmatch(r"[A-Z]{3}", config.base_currency):
        raise ConfigError(f"base_currency must be an ISO 4217 code like GBP, got {config.base_currency!r}")
    return config


def _sources(raw: object) -> dict[str, SourceConfig]:
    if not isinstance(raw, dict):
        raise ConfigError("sources must be a table of [sources.<name>] sections")
    merged = dict(DEFAULT_SOURCES)
    for name, table in raw.items():
        if not isinstance(table, dict):
            raise ConfigError(f"sources.{name} must be a table")
        _reject_unknown(table, SourceConfig, f"sources.{name}")
        base = merged.get(name)
        if base is None and "rate_per_second" not in table:
            raise ConfigError(f"sources.{name} is new and needs rate_per_second")
        values = {}
        for key, value in table.items():
            wanted = int if key == "max_retries" else (int, float)
            if isinstance(value, bool) or not isinstance(value, wanted) or value <= 0:
                kind = "a positive whole number" if key == "max_retries" else "a positive number"
                raise ConfigError(f"sources.{name}.{key} must be {kind}, got {value!r}")
            values[key] = value if key == "max_retries" else float(value)
        merged[name] = replace(base, **values) if base else SourceConfig(**values)
    return merged


def _resolve(config: Config, base: Path) -> Config:
    # Relative to the config file, not the cwd: a scheduled task starts elsewhere and
    # would otherwise silently create a second, empty database.
    if config.db_path.is_absolute():
        return config
    return replace(config, db_path=base / config.db_path)
