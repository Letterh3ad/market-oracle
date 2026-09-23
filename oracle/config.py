from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
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
    "yahoo": SourceConfig(rate_per_second=0.5),
    "eodhd": SourceConfig(rate_per_second=2.0),
    "ecb": SourceConfig(rate_per_second=0.5),
    "frankfurter": SourceConfig(rate_per_second=0.5),
    # Its free tier asks for about one call a day; one update makes one call.
    "open_er_api": SourceConfig(rate_per_second=0.05),
    "gdelt": SourceConfig(rate_per_second=0.2),
    "google_news": SourceConfig(rate_per_second=0.5),
    "ddgs": SourceConfig(rate_per_second=0.2),
    "sec_edgar": SourceConfig(rate_per_second=5.0),
    "financedatabase": SourceConfig(rate_per_second=0.5),
}


PRICE_SOURCES = ("yahoo", "eodhd")
FX_SOURCES = ("yahoo", "ecb", "frankfurter", "eodhd", "open_er_api")
KEY_NAMES = ("eodhd",)


@dataclass(frozen=True)
class PricesConfig:
    sources: tuple[str, ...] = PRICE_SOURCES
    """Tried in order for each listing until one has data; keyed ones only with a key."""


@dataclass(frozen=True)
class FxConfig:
    sources: tuple[str, ...] = FX_SOURCES
    """Tried in order for each currency until one has rates; keyed ones only with a key."""


@dataclass(frozen=True)
class AnalystsConfig:
    peers: int = 20
    """Companies a factor score is compared against."""


PEERS_RANGE = (5, 100)


@dataclass(frozen=True)
class RiskConfig:
    """Paper-account sizing rules; the design's conservative defaults."""

    account: float = 10_000.0
    """Paper account size, in the base currency."""
    max_position: float = 0.10
    """Largest share of the account one position may take."""
    cash_floor: float = 0.20
    """Share of the account always kept in cash."""
    target_volatility: float = 0.20
    """Yearly price swing at which a position gets the full `max_position`."""


@dataclass(frozen=True)
class StrategyConfig:
    """The strategy account's rules beyond `[risk]` sizing; see DECISIONS.md."""

    min_probability: float = 0.6
    """Buy only on a buy or strong buy lean at least this likely."""
    max_positions: int = 10
    stop_loss: float = 0.08
    """Sell a position this far below its average cost."""


# Shares of the account: above 0 and below 1 (a 100% cash floor would forbid trading).
@dataclass(frozen=True)
class GuiConfig:
    host: str = "127.0.0.1"
    """Localhost only by default: nothing on the network can reach the GUI."""
    port: int = 8740


_RISK_SHARES = ("max_position", "cash_floor", "target_volatility")


class Keys:
    """API keys for paid sources. Never shown: repr lists names only."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def with_(self, name: str, value: str) -> Keys:
        return Keys({**self._values, name: value})

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Keys) and other._values == self._values

    def __repr__(self) -> str:
        return f"Keys({', '.join(sorted(self._values))})"


@dataclass(frozen=True)
class Config:
    db_path: Path = Path("oracle.db")
    base_currency: str = "GBP"
    sources: dict[str, SourceConfig] = field(default_factory=lambda: dict(DEFAULT_SOURCES))
    prices: PricesConfig = field(default_factory=PricesConfig)
    fx: FxConfig = field(default_factory=FxConfig)
    analysts: AnalystsConfig = field(default_factory=AnalystsConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
    keys: Keys = field(default_factory=Keys)


def load_config(path: Path | None, cwd: Path | None = None) -> tuple[Config, Path | None]:
    """Return the config and the file it came from, or None when defaults were used."""
    cwd = cwd or Path.cwd()
    if path is None:
        path = cwd / DEFAULT_CONFIG_NAME
        if not path.is_file():
            return _with_env_keys(_resolve(Config(), cwd)), None
    elif not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return _with_env_keys(_resolve(_apply(raw), path.parent)), path


def _with_env_keys(config: Config) -> Config:
    # The environment wins, so a key can stay out of any file.
    keys = config.keys
    for name in KEY_NAMES:
        value = os.environ.get(f"ORACLE_{name.upper()}_KEY")
        if value:
            keys = keys.with_(name, value)
    return replace(config, keys=keys)


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
        if name == "keys":
            values[name] = _keys(value)
            continue
        if name == "prices":
            values[name] = PricesConfig(sources=_source_list(value, "prices", PRICE_SOURCES, PricesConfig))
            continue
        if name == "fx":
            values[name] = FxConfig(sources=_source_list(value, "fx", FX_SOURCES, FxConfig))
            continue
        if name == "analysts":
            values[name] = _analysts(value)
            continue
        if name == "risk":
            values[name] = _risk(value)
            continue
        if name == "strategy":
            values[name] = _strategy(value)
            continue
        if name == "gui":
            values[name] = _gui(value)
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


def _analysts(raw: object) -> AnalystsConfig:
    if not isinstance(raw, dict):
        raise ConfigError("analysts must be an [analysts] table")
    _reject_unknown(raw, AnalystsConfig, "[analysts]")
    peers = raw.get("peers", AnalystsConfig.peers)
    low, high = PEERS_RANGE
    if isinstance(peers, bool) or not isinstance(peers, int) or not low <= peers <= high:
        raise ConfigError(f"analysts.peers must be a whole number from {low} to {high}, got {peers!r}")
    return AnalystsConfig(peers=peers)


def _risk(raw: object) -> RiskConfig:
    if not isinstance(raw, dict):
        raise ConfigError("risk must be a [risk] table")
    _reject_unknown(raw, RiskConfig, "[risk]")
    for key, value in raw.items():
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        if key in _RISK_SHARES:
            ok = ok and value < 1
        if not ok:
            wanted = "a share above 0 and below 1" if key in _RISK_SHARES else "a positive number"
            raise ConfigError(f"risk.{key} must be {wanted}, got {value!r}")
    return RiskConfig(**{k: float(v) for k, v in raw.items()})


def _strategy(raw: object) -> StrategyConfig:
    if not isinstance(raw, dict):
        raise ConfigError("strategy must be a [strategy] table")
    _reject_unknown(raw, StrategyConfig, "[strategy]")
    for key, value in raw.items():
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if key == "max_positions":
            ok, wanted = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 50, "1 to 50"
        elif key == "min_probability":
            ok, wanted = number and 0 < value <= 1, "above 0, at most 1"
        else:
            ok, wanted = number and 0 < value < 1, "above 0 and below 1"
        if not ok:
            raise ConfigError(f"strategy.{key} must be {wanted}, got {value!r}")
    return StrategyConfig(**raw)


def _gui(raw: object) -> GuiConfig:
    if not isinstance(raw, dict):
        raise ConfigError("gui must be a [gui] table")
    _reject_unknown(raw, GuiConfig, "[gui]")
    host, port = raw.get("host", GuiConfig.host), raw.get("port", GuiConfig.port)
    if not isinstance(host, str) or not host:
        raise ConfigError(f"gui.host must be an address like 127.0.0.1, got {host!r}")
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ConfigError(f"gui.port must be a whole number from 1024 to 65535, got {port!r}")
    return GuiConfig(host=host, port=port)


def _keys(raw: object) -> Keys:
    if not isinstance(raw, dict):
        raise ConfigError("keys must be a [keys] table")
    unknown = sorted(set(raw) - set(KEY_NAMES))
    if unknown:
        raise ConfigError(f"unknown key(s) in [keys]: {', '.join(unknown)}; known: {', '.join(KEY_NAMES)}")
    for name, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"keys.{name} must be a non-empty string")
    return Keys({name: value.strip() for name, value in raw.items()})


def _source_list(raw: object, section: str, known: tuple[str, ...], cls: type) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{section} must be a [{section}] table")
    _reject_unknown(raw, cls, f"[{section}]")
    names = raw.get("sources", [])
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ConfigError(f"{section}.sources must be a non-empty list of names")
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ConfigError(f"{section}.sources: unknown {', '.join(unknown)}; known: {', '.join(known)}")
    return tuple(names)


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
