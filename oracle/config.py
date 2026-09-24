from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

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
    # Asks for one request per 5 seconds and answers a quicker one with a longer lockout
    # (seen 2026-09-23), so half that pace and one retry.
    "gdelt": SourceConfig(rate_per_second=0.1, max_retries=1),
    "google_news": SourceConfig(rate_per_second=0.5),
    "ddgs": SourceConfig(rate_per_second=0.2),
    "sec_edgar": SourceConfig(rate_per_second=5.0),
    # Pages the research loop reads, on any site. A short cooldown: three paywalled 403s in
    # a row say little about the next site.
    "web": SourceConfig(rate_per_second=1.0, cooldown_s=120.0, max_retries=1),
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


DEFAULT_BENCHMARKS = ("VWRL.L", "VUSA.L")
"""Vanguard FTSE All-World and Vanguard S&P 500, London-listed and GBP-priced, so a GBP
base currency reads them with no FX noise."""


@dataclass(frozen=True)
class CompareConfig:
    benchmarks: tuple[str, ...] = DEFAULT_BENCHMARKS
    """Market index lines shown beside the accounts in the Ledgers comparison: priced
    alongside watched listings, but never traded, researched, analysed or watchlisted."""


@dataclass(frozen=True)
class ScheduleConfig:
    """Automatic ticks while the GUI runs. Off until the user turns it on."""

    enabled: bool = False
    time: str = "22:30"
    """Local time, weekdays only: after the US close for a UK user."""
    warn_minutes: int = 10
    """The GUI warns this long before a scheduled tick, offering postpone and skip; 0 off."""
    research_when_idle: bool = False
    """Research in a tick only once the PC has been idle `idle_minutes`; otherwise it waits."""
    idle_minutes: int = 15
    midday_time: str = "12:30"
    """A second weekday slot, local time, for discovery and research only; "" off."""
    midday_budget_min: int = 60
    """How long the midday pass may take, discovery included."""


WARN_RANGE = (0, 120)
IDLE_RANGE = (1, 240)
MIDDAY_BUDGET_RANGE = (1, 480)


@dataclass(frozen=True)
class AutomationConfig:
    """The master switch: off, nothing starts by itself (manual buttons still work)."""

    enabled: bool = True


@dataclass(frozen=True)
class AiAccountConfig:
    """The paper account that trades research verdicts. Off until the user turns it on."""

    enabled: bool = False
    max_verdict_age_days: int = 7
    """An older verdict is never traded, and a tick researches the listing again."""
    move_threshold: float = 0.05
    """A tick researches a listing again once its price moved this share since the report."""
    research_budget_min: int = 120
    """How long a tick may spend starting research runs; the rest waits for the next tick."""


VERDICT_AGE_RANGE = (1, 90)
RESEARCH_BUDGET_RANGE = (1, 480)


@dataclass(frozen=True)
class DiscoveryConfig:
    """Finding companies beyond the watchlists and adding them to the `discovered` list."""

    enabled: bool = True
    per_tick: int = 1
    """Companies one run may add."""
    max_total: int = 10
    """The discovered list never holds more."""
    never: tuple[str, ...] = ("Tesla",)
    """Company names never discovered, put on the discovered list or bought from it, matched
    as whole words on every listing's name. Tesla is never watched or traded whatever this says."""
    ai_trades: bool = True
    """The AI account trades discovered listings' verdicts; off, it leaves them alone."""
    themes: tuple[str, ...] = ()
    """Short topics searched in the news ("AI chips"); none, and the theme source is skipped."""
    search_related: bool = True
    """Look beside the watched companies: same industry, or named with them in the news."""
    search_themes: bool = True
    search_movers: bool = True
    """Look in news of market movers across the watched listings' countries."""


PER_TICK_RANGE = (1, 20)
MAX_TOTAL_RANGE = (1, 500)
MAX_THEME = 60


@dataclass(frozen=True)
class UniverseConfig:
    suffix_blind_grouping: bool = False
    """Join companies whose names differ only in legal forms (plc, Inc., AG); applied at the
    next universe refresh. Off until the user has reviewed the merges it proposes."""


@dataclass(frozen=True)
class GuiConfig:
    host: str = "127.0.0.1"
    """Localhost only by default: nothing on the network can reach the GUI."""
    port: int = 8740


@dataclass(frozen=True)
class LlmConfig:
    backend: str = "ollama"
    model: str = "qwen3:4b-instruct-2507-q8_0"
    """Measured in the stalker: fits an 8GB GPU with room for Laya."""
    base_url: str = "http://localhost:11434"
    max_steps: int = 16
    """Tool calls a run may make before it must answer."""
    timeout_s: float = 180.0
    """Per reply; a cold model load on 8GB is slow."""
    context: int = 16384
    """Tokens of context asked of the model; Ollama's own default is too small for a run."""
    claude_model: str = "claude-opus-5"
    """The model when backend is "claude"; its key comes from ANTHROPIC_API_KEY, never this file."""


@dataclass(frozen=True)
class ResearchConfig:
    contact: str | None = None
    """Name and email for SEC's User-Agent rule; sent to sec.gov only. Unset, EDGAR is skipped."""
    read_articles: int = 12
    """Articles a run reads and notes after its searches, beyond those already noted."""


READ_ARTICLES_RANGE = (1, 30)


# Shares of the account: above 0 and below 1 (a 100% cash floor would forbid trading).
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
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    compare: CompareConfig = field(default_factory=CompareConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    automation: AutomationConfig = field(default_factory=AutomationConfig)
    ai_account: AiAccountConfig = field(default_factory=AiAccountConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    keys: Keys = field(default_factory=Keys)


EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "oracle.toml.example"
SECRET_SECTIONS = ("keys",)


@dataclass(frozen=True)
class Setting:
    """One oracle.toml key. `section` "" is the top level."""

    section: str
    key: str
    kind: str
    """bool, int, float, str or list."""
    default: object

    @property
    def secret(self) -> bool:
        return self.section in SECRET_SECTIONS

    def meaning_in(self, meanings: Mapping[tuple[str, str], str]) -> str | None:
        # Every [sources.<name>] table has the same keys; the example documents one.
        return meanings.get((self.section.split(".")[0], self.key))


def setting_list(extra_sources: tuple[str, ...] = ()) -> list[Setting]:
    """Every oracle.toml key with its default, in Config's order; `extra_sources` adds
    [sources.<name>] tables a file defines beyond the built-ins."""
    out: list[Setting] = []
    defaults = Config()
    for f in fields(Config):
        value = getattr(defaults, f.name)
        if f.name == "sources":
            names = list(DEFAULT_SOURCES) + [n for n in extra_sources if n not in DEFAULT_SOURCES]
            for name in names:
                base = DEFAULT_SOURCES.get(name)
                out += [_setting(f"sources.{name}", s.name, getattr(base, s.name) if base else None, s.type)
                        for s in fields(SourceConfig)]
        elif f.name == "keys":
            out += [Setting("keys", name, "str", None) for name in KEY_NAMES]
        elif hasattr(value, "__dataclass_fields__"):
            out += [_setting(f.name, s.name, getattr(value, s.name), s.type) for s in fields(value)]
        else:
            out.append(_setting("", f.name, value, f.type))
    return out


def _setting(section: str, key: str, default: object, annotation: str) -> Setting:
    if isinstance(default, Path):
        default = default.as_posix()
    elif isinstance(default, tuple):
        default = list(default)
    kind = "list" if isinstance(default, list) else annotation if annotation in ("bool", "int", "float") else "str"
    return Setting(section, key, kind, default)


_EXAMPLE_KEY = re.compile(r'^(#\s*)?([a-z_]+)\s*=\s*("[^"]*"|\[[^\]]*\]|[^\s#]+)\s*#\s*(.+)$')


def example_meanings(text: str) -> dict[tuple[str, str], str]:
    """(section, key) -> the inline comment on that key in oracle.toml.example, commented-out
    keys included. [sources.<name>] tables all read as "sources"."""
    out: dict[tuple[str, str], str] = {}
    section = ""
    for line in text.splitlines():
        header = re.match(r"^\[([\w.]+)\]", line)
        if header:
            section = header.group(1).split(".")[0]
            continue
        match = _EXAMPLE_KEY.match(line.strip())
        if match and (section, match.group(2)) not in out:
            out[section, match.group(2)] = match.group(4).strip()
    return out


def with_settings(text: str, section: str, values: Mapping[str, object]) -> str:
    """`text` (an oracle.toml) with each key in `[section]` set to its value, or removed when
    None. `section` "" is the top level; "sources.web" a nested table. Everything else,
    comments included, is kept as it was."""
    try:
        doc = tomlkit.parse(text)
    except TOMLKitError as exc:
        raise ConfigError(f"not valid TOML: {exc}") from exc
    table = doc
    parts = section.split(".") if section else []
    for depth, part in enumerate(parts):
        inner = table.get(part)
        if inner is None:
            inner = table[part] = tomlkit.table(is_super_table=depth < len(parts) - 1)
        table = inner
    for key, value in values.items():
        if value is None:
            table.pop(key, None)
        else:
            table[key] = value
    return tomlkit.dumps(doc)


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
        value = os.environ.get(key_env_var(name))
        if value:
            keys = keys.with_(name, value)
    return replace(config, keys=keys)


def key_env_var(name: str) -> str:
    return f"ORACLE_{name.upper()}_KEY"


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
        if name == "universe":
            values[name] = _universe(value)
            continue
        if name == "risk":
            values[name] = _risk(value)
            continue
        if name == "strategy":
            values[name] = _strategy(value)
            continue
        if name == "compare":
            values[name] = _compare(value)
            continue
        if name == "gui":
            values[name] = _gui(value)
            continue
        if name == "schedule":
            values[name] = _schedule(value)
            continue
        if name == "automation":
            values[name] = _automation(value)
            continue
        if name == "ai_account":
            values[name] = _ai_account(value)
            continue
        if name == "discovery":
            values[name] = _discovery(value)
            continue
        if name == "llm":
            values[name] = _llm(value)
            continue
        if name == "research":
            values[name] = _research(value)
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
    return AnalystsConfig(peers=check_peers(raw.get("peers", AnalystsConfig.peers)))


def check_peers(peers: object) -> int:
    low, high = PEERS_RANGE
    if isinstance(peers, bool) or not isinstance(peers, int) or not low <= peers <= high:
        raise ConfigError(f"analysts.peers must be a whole number from {low} to {high}, got {peers!r}")
    return peers


def _universe(raw: object) -> UniverseConfig:
    if not isinstance(raw, dict):
        raise ConfigError("universe must be a [universe] table")
    _reject_unknown(raw, UniverseConfig, "[universe]")
    blind = raw.get("suffix_blind_grouping", UniverseConfig.suffix_blind_grouping)
    if not isinstance(blind, bool):
        raise ConfigError(f"universe.suffix_blind_grouping must be true or false, got {blind!r}")
    return UniverseConfig(suffix_blind_grouping=blind)


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


def _compare(raw: object) -> CompareConfig:
    if not isinstance(raw, dict):
        raise ConfigError("compare must be a [compare] table")
    _reject_unknown(raw, CompareConfig, "[compare]")
    benchmarks = raw.get("benchmarks", list(DEFAULT_BENCHMARKS))
    if not isinstance(benchmarks, list) or not all(isinstance(b, str) and b.strip() for b in benchmarks):
        raise ConfigError(f"compare.benchmarks must be a list of symbols, got {benchmarks!r}")
    return CompareConfig(benchmarks=tuple(b.strip() for b in benchmarks))


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


def _automation(raw: object) -> AutomationConfig:
    if not isinstance(raw, dict):
        raise ConfigError("automation must be an [automation] table")
    _reject_unknown(raw, AutomationConfig, "[automation]")
    enabled = raw.get("enabled", AutomationConfig.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError(f"automation.enabled must be true or false, got {enabled!r}")
    return AutomationConfig(enabled=enabled)


def _ai_account(raw: object) -> AiAccountConfig:
    if not isinstance(raw, dict):
        raise ConfigError("ai_account must be an [ai_account] table")
    return check_ai_account(raw)


def check_ai_account(raw: Mapping[str, object]) -> AiAccountConfig:
    """`[ai_account]` from its keys, defaults for the rest; raises ConfigError naming the bad one."""
    _reject_unknown(raw, AiAccountConfig, "[ai_account]")
    ai = replace(AiAccountConfig(), **raw)
    checks = {
        "enabled": (isinstance(ai.enabled, bool), "true or false"),
        "max_verdict_age_days": (_whole(ai.max_verdict_age_days, *VERDICT_AGE_RANGE),
                                 "a whole number from {} to {}".format(*VERDICT_AGE_RANGE)),
        "move_threshold": (isinstance(ai.move_threshold, (int, float)) and not isinstance(ai.move_threshold, bool)
                           and 0 < ai.move_threshold < 1, "a share above 0 and below 1"),
        "research_budget_min": (_whole(ai.research_budget_min, *RESEARCH_BUDGET_RANGE),
                                "a whole number of minutes from {} to {}".format(*RESEARCH_BUDGET_RANGE)),
    }
    for key, (ok, wanted) in checks.items():
        if not ok:
            raise ConfigError(f"ai_account.{key} must be {wanted}, got {getattr(ai, key)!r}")
    return replace(ai, move_threshold=float(ai.move_threshold))


def _discovery(raw: object) -> DiscoveryConfig:
    if not isinstance(raw, dict):
        raise ConfigError("discovery must be a [discovery] table")
    _reject_unknown(raw, DiscoveryConfig, "[discovery]")
    d = replace(DiscoveryConfig(), **raw)
    never, themes = d.never, d.themes
    checks = {
        **{key: (isinstance(getattr(d, key), bool), "true or false") for key in (
            "enabled", "ai_trades", "search_related", "search_themes", "search_movers")},
        "per_tick": (_whole(d.per_tick, *PER_TICK_RANGE), "a whole number from {} to {}".format(*PER_TICK_RANGE)),
        "max_total": (_whole(d.max_total, *MAX_TOTAL_RANGE), "a whole number from {} to {}".format(*MAX_TOTAL_RANGE)),
        "never": (isinstance(never, (list, tuple)) and all(isinstance(n, str) and n.strip() for n in never),
                  'a list of company names like ["Tesla"]'),
        "themes": (isinstance(themes, (list, tuple))
                   and all(isinstance(t, str) and 0 < len(t.strip()) <= MAX_THEME for t in themes),
                   f'a list of short topics like ["AI chips", "defence"], each at most {MAX_THEME} characters'),
    }
    for key, (ok, wanted) in checks.items():
        if not ok:
            raise ConfigError(f"discovery.{key} must be {wanted}, got {getattr(d, key)!r}")
    return replace(d, never=tuple(n.strip() for n in never), themes=tuple(dict.fromkeys(t.strip() for t in themes)))


def _schedule(raw: object) -> ScheduleConfig:
    if not isinstance(raw, dict):
        raise ConfigError("schedule must be a [schedule] table")
    return check_schedule(raw)


def _whole(value: object, low: int, high: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def check_schedule(raw: Mapping[str, object]) -> ScheduleConfig:
    """`[schedule]` from its keys, defaults for the rest; raises ConfigError naming the bad one."""
    _reject_unknown(raw, ScheduleConfig, "[schedule]")
    s = replace(ScheduleConfig(), **raw)
    checks = {
        "enabled": (isinstance(s.enabled, bool), "true or false"),
        "research_when_idle": (isinstance(s.research_when_idle, bool), "true or false"),
        "warn_minutes": (_whole(s.warn_minutes, *WARN_RANGE), "a whole number of minutes from {} to {}".format(
            *WARN_RANGE)),
        "idle_minutes": (_whole(s.idle_minutes, *IDLE_RANGE), "a whole number of minutes from {} to {}".format(
            *IDLE_RANGE)),
        "midday_budget_min": (_whole(s.midday_budget_min, *MIDDAY_BUDGET_RANGE),
                              "a whole number of minutes from {} to {}".format(*MIDDAY_BUDGET_RANGE)),
    }
    for key, (ok, wanted) in checks.items():
        if not ok:
            raise ConfigError(f"schedule.{key} must be {wanted}, got {getattr(s, key)!r}")
    check_schedule_time(s.time)
    if s.midday_time != "":
        check_schedule_time(s.midday_time, "midday_time", ' or "" (off)')
    return s


def check_schedule_time(value: object, key: str = "time", also: str = "") -> str:
    if not (isinstance(value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value)):
        raise ConfigError(f'schedule.{key} must be a 24-hour time like "22:30"{also}, got {value!r}')
    return value


LLM_BACKENDS = ("ollama", "claude")


def _llm(raw: object) -> LlmConfig:
    if not isinstance(raw, dict):
        raise ConfigError("llm must be an [llm] table")
    _reject_unknown(raw, LlmConfig, "[llm]")
    out = {}
    for key, value in raw.items():
        whole = isinstance(value, int) and not isinstance(value, bool)
        if key == "backend":
            ok, wanted = value in LLM_BACKENDS, f"one of {', '.join(LLM_BACKENDS)}"
        elif key in ("model", "claude_model"):
            ok, wanted = isinstance(value, str) and bool(value.strip()), "a model name like qwen3:8b"
        elif key == "base_url":
            ok, wanted = isinstance(value, str) and re.match(r"https?://", value) is not None, "an http(s) URL"
        elif key == "max_steps":
            ok, wanted = whole and 1 <= value <= 50, "a whole number from 1 to 50"
        elif key == "context":
            ok, wanted = whole and 2048 <= value <= 262144, "a whole number of tokens from 2048 to 262144"
        else:
            ok, wanted = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0, "a positive number"
            value = float(value) if ok else value
        if not ok:
            raise ConfigError(f"llm.{key} must be {wanted}, got {value!r}")
        out[key] = value
    return LlmConfig(**out)


def _research(raw: object) -> ResearchConfig:
    if not isinstance(raw, dict):
        raise ConfigError("research must be a [research] table")
    _reject_unknown(raw, ResearchConfig, "[research]")
    contact = raw.get("contact")
    if contact is not None and not (isinstance(contact, str) and re.search(r"\S+@\S+\.\S+", contact)):
        raise ConfigError(f'research.contact must be a name and email like "Jo Bloggs jo@example.com", got {contact!r}')
    read = raw.get("read_articles", ResearchConfig.read_articles)
    if not _whole(read, *READ_ARTICLES_RANGE):
        raise ConfigError("research.read_articles must be a whole number from {} to {}, got {!r}".format(
            *READ_ARTICLES_RANGE, read))
    return ResearchConfig(contact=contact.strip() if contact else None, read_articles=read)


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
