from __future__ import annotations

import ipaddress
import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import tomlkit
from tomlkit.exceptions import TOMLKitError

log = logging.getLogger(__name__)

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
    # Name searches for discovery, kept apart so their failures never pause prices.
    "yahoo_search": SourceConfig(rate_per_second=0.5, max_retries=1),
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
    # Index member pages: a few dozen a week, well inside Wikimedia's asks of scripted readers.
    "wikipedia": SourceConfig(rate_per_second=0.2),
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
    """How long the midday pass may start work, discovery included; a research run started
    near the end finishes past it."""
    fill_times: tuple[str, ...] = ("09:30", "15:15")
    """Weekday fill passes, local time: prices, FX and fills only, so an order placed by the
    evening tick fills at the next open the same day; () off. Each at least
    `FILL_CLEARANCE_MIN` from the tick, the midday pass and each other."""


WARN_RANGE = (0, 120)
IDLE_RANGE = (1, 240)
MIDDAY_BUDGET_RANGE = (1, 480)
FILL_CLEARANCE_MIN = 30
BLANK_OFF = {("schedule", "midday_time")}
"""Keys an empty value turns off, rather than resetting to the default."""


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


@dataclass(frozen=True)
class RegimeAccountConfig:
    """The paper account that trades the regime blend (rules only). On by default: it needs no
    model, so its record starts the day it ships."""

    enabled: bool = True


@dataclass(frozen=True)
class RegimeAiAccountConfig:
    """The paper account that trades the regime blend with a daily local-model tilt. On by
    default, like the rules-only line, so the two records start together."""

    enabled: bool = True
    model: str = ""
    """The model profile that tilts the weights; "" is the research slot's."""


@dataclass(frozen=True)
class RegimeV2AccountConfig:
    """The paper account that trades the regime-v2 book. On by default, like the other regime
    lines, so its record starts the day it ships."""

    enabled: bool = True


@dataclass(frozen=True)
class CoreSatAccountConfig:
    """The paper account that holds 75% in an equal-weight buy and hold of the main list and
    trades 25% by the regime blend. On by default, so its record starts the day it ships."""

    enabled: bool = True


@dataclass(frozen=True)
class LayaConfig:
    """Laya, the small model that scores headlines per company (`research.laya`)."""

    enabled: bool = True
    device: str = "auto"
    """auto (the GPU when it has room beside Ollama, else the CPU), gpu or cpu."""
    industry_daily: bool = True
    """The tick's industry research: new headlines of held, watched and main-list companies and their
    industry maps, scored with Laya per related pair."""
    industry_requests: int = 400
    """News searches one tick's industry research may make."""
    industry_minutes: int = 20
    """Minutes one tick's industry research may take, searches and scoring together."""


@dataclass(frozen=True)
class RegimeV2Config:
    """regime-v2's rotation (see `broker.regime.Rotation`); its weights are `[regime]`'s. Set a
    priori, not fitted to a backtest."""

    rotation_min_hold_days: int = 20
    """Trading days a holding is kept before it can be rotated out."""
    rotation_margin: float = 0.25
    """How far under the best buy not held a holding's blended score must be to be sold for it."""
    rotation_max: int = 2
    """Rotations a step."""


MIN_HOLD_RANGE = (1, 250)
ROTATIONS_RANGE = (1, 10)
VERDICT_AGE_RANGE = (1, 90)
RESEARCH_BUDGET_RANGE = (1, 480)


@dataclass(frozen=True)
class RegimeConfig:
    """The regime lines' preset weights per mood (priors from the literature) and knobs; see
    `broker.regime`. Weight keys are `<mood>_<method>`, each mood renormalised."""

    calm_up_trend: float = 0.15
    calm_up_momentum: float = 0.25
    calm_up_reversion: float = 0.05
    calm_up_low_vol: float = 0.05
    calm_up_quality_value: float = 0.10
    calm_up_earnings_drift: float = 0.10
    calm_up_hold: float = 0.15
    calm_up_ai_verdict: float = 0.15
    volatile_up_trend: float = 0.10
    volatile_up_momentum: float = 0.10
    volatile_up_reversion: float = 0.15
    volatile_up_low_vol: float = 0.20
    volatile_up_quality_value: float = 0.10
    volatile_up_earnings_drift: float = 0.10
    volatile_up_hold: float = 0.10
    volatile_up_ai_verdict: float = 0.15
    calm_down_trend: float = 0.25
    calm_down_momentum: float = 0.10
    calm_down_reversion: float = 0.10
    calm_down_low_vol: float = 0.20
    calm_down_quality_value: float = 0.15
    calm_down_earnings_drift: float = 0.05
    calm_down_hold: float = 0.00
    calm_down_ai_verdict: float = 0.15
    volatile_down_trend: float = 0.20
    volatile_down_momentum: float = 0.00
    volatile_down_reversion: float = 0.15
    volatile_down_low_vol: float = 0.25
    volatile_down_quality_value: float = 0.15
    volatile_down_earnings_drift: float = 0.05
    volatile_down_hold: float = 0.05
    volatile_down_ai_verdict: float = 0.15
    smoothing_span: int = 5
    """Trading days the final weights are smoothed over."""
    sector_share: float = 0.3
    """At most ceil(this x max_positions) holdings plus buys per sector."""
    warning_factor: float = 0.5
    """Each firing playbook warning multiplies its method's weight by this."""
    good_factor: float = 1.25
    playbook_cap: float = 1.5
    """The playbook's combined multiplier is clipped to [0, this]."""
    tilt_cap: float = 0.5
    """The AI tilt multiplies a weight within [1 - this, 1 + this]."""


REGIME_MOODS = ("calm_up", "volatile_up", "calm_down", "volatile_down")
SPAN_RANGE = (1, 60)


@dataclass(frozen=True)
class DiscoveryConfig:
    """Finding companies beyond the watchlists and adding them to the `discovered` list."""

    enabled: bool = True
    per_tick: int = 1
    """Companies one run may add."""
    max_total: int = 10
    """The discovered list never holds more."""
    per_question: int = 2
    """Companies one auto question may have on the discovered list at once."""
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
PER_QUESTION_RANGE = (1, 20)
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


DEFAULT_PROFILE = "default"
CLAUDE_CONTEXT = 200_000
"""Conservative for every Claude model the API can name."""


@dataclass(frozen=True)
class ModelProfile:
    """One `[models.<name>]` table: a model some task can run on. As parsed, None means
    unset; model_profiles fills those in from the provider."""

    provider: str
    model: str
    base_url: str | None = None
    """None once resolved: the provider's own endpoint."""
    context: int | None = None
    """Tokens the model reads at once; sizes prompts, and Ollama's window."""
    timeout_s: float | None = None
    """Per reply."""
    key_env: str | None = None
    """The environment variable holding the key."""
    key: str | None = field(default=None, repr=False)
    """The key itself, kept in oracle.toml; never shown."""
    claimed_cutoff: str | None = None
    """The model's stated training cutoff, YYYY-MM-DD; unset, DEFAULT_CLAIMED_CUTOFF. A cutoff
    probe never measures earlier than this."""
    unusable: str | None = field(default=None, metadata={"derived": True})
    """Why a profile the file holds may not be used (a rule it predates); never set in the file."""


DEFAULT_CLAIMED_CUTOFF = date(2025, 5, 20)
"""Qwen3's stated training cutoff, the default model's."""

PROFILE_KEYS_IN_FILE = tuple(f.name for f in fields(ModelProfile) if not f.metadata.get("derived"))


@dataclass(frozen=True)
class Provider:
    base_url: str | None
    """None: the provider's own endpoint, or none it could default to (`base_url_required`, or no `url`)."""
    context: int
    keyed: bool
    """Whether it takes a key."""
    fallback_key: str | None = None
    """Where it looks with no key or key_env; None: it goes without one."""
    base_url_required: bool = False
    url: bool = True
    """False: it reaches no endpoint, so takes no base_url."""


PROVIDERS: dict[str, Provider] = {
    "ollama": Provider(LlmConfig.base_url, LlmConfig.context, keyed=False),
    "claude_api": Provider(None, CLAUDE_CONTEXT, keyed=True, fallback_key="env / ant login"),
    # Local servers need no key, and OPENAI_API_KEY is never read: a key goes only where a profile sends it.
    "openai_compatible": Provider(None, LlmConfig.context, keyed=True, base_url_required=True),
    "claude_code": Provider(None, CLAUDE_CONTEXT, keyed=False, url=False),
}
"""Every provider a profile can name, with what its unset keys mean."""
PROFILE_CONTEXT_RANGE = (2048, 2_000_000)
URLLESS_PROVIDERS = tuple(name for name, spec in PROVIDERS.items() if not spec.url)
CLAUDE_CODE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]*")
"""On Windows the CLI is often claude.cmd, whose arguments cmd.exe reparses: only plain names pass."""
ALLOW_CLAUDE_CODE = "allow_claude_code"
"""The `[models]` key, beside the profile tables, that lets a profile name claude_code: it
drives the user's own claude.ai login, which a distributed build must not offer."""
PROVIDER_KEYS = ("base_url", "context")
"""Keys whose meaning belongs to one provider: a value carried over from another is dropped."""


LLM_PROFILE_KEYS = ("backend", "model", "base_url", "timeout_s", "context", "claude_model")
"""The `[llm]` keys that describe the default profile; `[models.default]` makes them moot."""

SLOTS_TABLE = "slots"
"""`[models.slots]`: which profile each model task runs on. Never a profile's name."""
SLOTS = ("research", "notes", "roles", "queries", "verdict", "discovery", "ai_account")
"""research: the loop; notes: article notes; roles: the report's roles; queries: query planning;
verdict; discovery: chat and tick discovery; ai_account: every stage of the research a tick
starts for the AI account. Unset, a slot runs on `default`, except ai_account: unset, that
research uses the other slots like any run."""


def model_profiles(config: Config) -> dict[str, ModelProfile]:
    """Every profile, `default` first, with the provider's defaults filled in."""
    return {name: resolved(p) for name, p in profile_tables(config).items()}


def profile_tables(config: Config) -> dict[str, ModelProfile]:
    """Every profile as set, `default` first: `[models.default]`, else what `[llm]` sets."""
    return {DEFAULT_PROFILE: config.models.get(DEFAULT_PROFILE, _from_llm(config.llm)), **config.models}


def resolved(p: ModelProfile) -> ModelProfile:
    spec = PROVIDERS[p.provider]
    return replace(p, base_url=p.base_url or spec.base_url, context=p.context or spec.context,
                   timeout_s=p.timeout_s or LlmConfig.timeout_s)


def _from_llm(llm: LlmConfig) -> ModelProfile:
    # Only what differs from the provider's defaults counts as set, so switching the
    # default's provider in the GUI carries none of the old provider's values over.
    timeout = llm.timeout_s if llm.timeout_s != LlmConfig.timeout_s else None
    if llm.backend == "claude":
        return ModelProfile("claude_api", llm.claude_model, timeout_s=timeout)
    spec = PROVIDERS["ollama"]
    return ModelProfile("ollama", llm.model, llm.base_url if llm.base_url != spec.base_url else None,
                        llm.context if llm.context != spec.context else None, timeout)


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
    regime_account: RegimeAccountConfig = field(default_factory=RegimeAccountConfig)
    regime_ai_account: RegimeAiAccountConfig = field(default_factory=RegimeAiAccountConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    regime_v2_account: RegimeV2AccountConfig = field(default_factory=RegimeV2AccountConfig)
    regime_v2: RegimeV2Config = field(default_factory=RegimeV2Config)
    core_sat_account: CoreSatAccountConfig = field(default_factory=CoreSatAccountConfig)
    laya: LayaConfig = field(default_factory=LayaConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    models: dict[str, ModelProfile] = field(default_factory=dict)
    """The `[models.<name>]` tables only; model_profiles adds the implicit default."""
    model_slots: dict[str, str] = field(default_factory=dict)
    """`[models.slots]`: slot -> profile, only the slots set. Parsed with `models`, never a top-level key."""
    allow_claude_code: bool = False
    """`[models] allow_claude_code`: set in the file by hand only, never in Settings."""
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
        elif f.name == "models":  # profiles are edited whole, in the Models section
            out += [Setting(f"models.{SLOTS_TABLE}", slot, "str", None) for slot in SLOTS]
        elif f.name in ("model_slots", ALLOW_CLAUDE_CODE):
            continue  # slots are listed under models; the flag is set only by hand
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


def without_section(text: str, section: str) -> str:
    """`text` with the `[section]` table ("models.big") removed, everything else kept. By
    lines, since tomlkit files the comments above the next header under the removed table."""
    lines = text.splitlines(keepends=True)
    header = re.compile(r"\s*\[\s*" + r"\s*\.\s*".join(map(re.escape, section.split("."))) + r"\s*\]\s*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        lines[start:end] = []
    out = "".join(lines)
    try:
        table = tomllib.loads(out)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"not valid TOML: {exc}") from exc
    for part in section.split("."):
        table = table.get(part) if isinstance(table, dict) else None
    if table is not None:
        raise ConfigError(f"[{section}] is not a plain table")
    return out


def load_config(path: Path | None, cwd: Path | None = None, strict: bool = False) -> tuple[Config, Path | None]:
    """Return the config and the file it came from, or None when defaults were used. A slot
    naming a profile that is gone is dropped with a warning, so an unattended start still
    runs; `strict` (a change someone is making now) refuses it instead."""
    cwd = cwd or Path.cwd()
    if path is None:
        path = cwd / DEFAULT_CONFIG_NAME
        if not path.is_file():
            return _with_env_keys(_resolve(Config(), cwd)), None
    elif not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as fh:
        try:
            raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc
    return _with_env_keys(_resolve(_apply(raw, strict), path.parent)), path


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
    unknown = sorted(set(raw) - {f.name for f in fields(cls) if not f.metadata.get("derived")})
    if unknown:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(unknown)}")


def _apply(raw: dict, strict: bool = False) -> Config:
    _reject_unknown(raw, Config, "oracle.toml")
    if "model_slots" in raw:
        raise ConfigError(f"unknown key(s) in oracle.toml: model_slots (slots go in [models.{SLOTS_TABLE}])")
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
        if name == "regime_account":
            values[name] = _regime_account(value)
            continue
        if name == "regime_ai_account":
            values[name] = _regime_ai_account(value)
            continue
        if name == "regime":
            values[name] = _regime(value)
            continue
        if name == "regime_v2_account":
            values[name] = _regime_v2_account(value)
            continue
        if name == "regime_v2":
            values[name] = _regime_v2(value)
            continue
        if name == "core_sat_account":
            values[name] = _switch(value, CoreSatAccountConfig, name)
            continue
        if name == "laya":
            values[name] = _laya(value)
            continue
        if name == "discovery":
            values[name] = _discovery(value)
            continue
        if name == "llm":
            values[name] = _llm(value)
            continue
        if name == "models":
            values[name], values[ALLOW_CLAUDE_CODE], values["model_slots"] = _models(value, strict)
            continue
        if name == ALLOW_CLAUDE_CODE:
            raise ConfigError(f"{name} belongs under [models]")
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


def _regime_account(raw: object) -> RegimeAccountConfig:
    if not isinstance(raw, dict):
        raise ConfigError("regime_account must be a [regime_account] table")
    _reject_unknown(raw, RegimeAccountConfig, "[regime_account]")
    enabled = raw.get("enabled", RegimeAccountConfig.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError(f"regime_account.enabled must be true or false, got {enabled!r}")
    return RegimeAccountConfig(enabled=enabled)


def _regime_ai_account(raw: object) -> RegimeAiAccountConfig:
    if not isinstance(raw, dict):
        raise ConfigError("regime_ai_account must be a [regime_ai_account] table")
    _reject_unknown(raw, RegimeAiAccountConfig, "[regime_ai_account]")
    config = replace(RegimeAiAccountConfig(), **raw)
    if not isinstance(config.enabled, bool):
        raise ConfigError(f"regime_ai_account.enabled must be true or false, got {config.enabled!r}")
    if not isinstance(config.model, str):
        raise ConfigError("regime_ai_account.model must be a model profile's name, or \"\" for the research "
                          f"slot's, got {config.model!r}")
    return config


def _regime(raw: object) -> RegimeConfig:
    if not isinstance(raw, dict):
        raise ConfigError("regime must be a [regime] table")
    _reject_unknown(raw, RegimeConfig, "[regime]")
    for key, value in raw.items():
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if key == "smoothing_span":
            low, high = SPAN_RANGE
            ok, wanted = isinstance(value, int) and not isinstance(value, bool) and low <= value <= high, f"{low} to {high}"
        elif key == "sector_share":
            ok, wanted = number and 0 < value <= 1, "above 0, at most 1"
        elif key in ("good_factor", "playbook_cap"):
            ok, wanted = number and 1 <= value <= 5, "1 to 5"
        elif key == "tilt_cap":
            ok, wanted = number and 0 <= value < 1, "at least 0 and below 1"
        else:
            ok, wanted = number and 0 <= value <= 1, "0 to 1"
        if not ok:
            raise ConfigError(f"regime.{key} must be {wanted}, got {value!r}")
    config = RegimeConfig(**{k: v if k == "smoothing_span" else float(v) for k, v in raw.items()})
    for mood in REGIME_MOODS:
        if not any(getattr(config, f.name) for f in fields(RegimeConfig) if f.name.startswith(mood + "_")):
            raise ConfigError(f"regime: every {mood}_ weight is 0; give at least one method a weight")
    return config


def _regime_v2_account(raw: object) -> RegimeV2AccountConfig:
    if not isinstance(raw, dict):
        raise ConfigError("regime_v2_account must be a [regime_v2_account] table")
    _reject_unknown(raw, RegimeV2AccountConfig, "[regime_v2_account]")
    config = replace(RegimeV2AccountConfig(), **raw)
    if not isinstance(config.enabled, bool):
        raise ConfigError(f"regime_v2_account.enabled must be true or false, got {config.enabled!r}")
    return config


def _switch(raw: object, kind: type, name: str):
    """A table holding only an `enabled` switch."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a [{name}] table")
    _reject_unknown(raw, kind, f"[{name}]")
    config = replace(kind(), **raw)
    if not isinstance(config.enabled, bool):
        raise ConfigError(f"{name}.enabled must be true or false, got {config.enabled!r}")
    return config


def _regime_v2(raw: object) -> RegimeV2Config:
    if not isinstance(raw, dict):
        raise ConfigError("regime_v2 must be a [regime_v2] table")
    _reject_unknown(raw, RegimeV2Config, "[regime_v2]")
    config = replace(RegimeV2Config(), **raw)
    margin = config.rotation_margin
    checks = {
        "rotation_min_hold_days": (_whole(config.rotation_min_hold_days, *MIN_HOLD_RANGE),
                                   "a whole number from {} to {}".format(*MIN_HOLD_RANGE)),
        "rotation_margin": (isinstance(margin, (int, float)) and not isinstance(margin, bool) and 0 < margin <= 2,
                            "above 0, at most 2"),
        "rotation_max": (_whole(config.rotation_max, *ROTATIONS_RANGE),
                         "a whole number from {} to {}".format(*ROTATIONS_RANGE)),
    }
    for key, (ok, wanted) in checks.items():
        if not ok:
            raise ConfigError(f"regime_v2.{key} must be {wanted}, got {getattr(config, key)!r}")
    return replace(config, rotation_margin=float(margin))


LAYA_DEVICES = ("auto", "gpu", "cpu")
INDUSTRY_REQUESTS_RANGE = (0, 5000)
INDUSTRY_MINUTES_RANGE = (1, 240)


def _laya(raw: object) -> LayaConfig:
    if not isinstance(raw, dict):
        raise ConfigError("laya must be a [laya] table")
    _reject_unknown(raw, LayaConfig, "[laya]")
    config = replace(LayaConfig(), **raw)
    if not isinstance(config.enabled, bool):
        raise ConfigError(f"laya.enabled must be true or false, got {config.enabled!r}")
    if config.device not in LAYA_DEVICES:
        raise ConfigError(f"laya.device must be one of {', '.join(LAYA_DEVICES)}, got {config.device!r}")
    if not isinstance(config.industry_daily, bool):
        raise ConfigError(f"laya.industry_daily must be true or false, got {config.industry_daily!r}")
    for key, (low, high) in (("industry_requests", INDUSTRY_REQUESTS_RANGE), ("industry_minutes", INDUSTRY_MINUTES_RANGE)):
        if not _whole(getattr(config, key), low, high):
            raise ConfigError(f"laya.{key} must be a whole number from {low} to {high}, got {getattr(config, key)!r}")
    return config


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
        "per_question": (_whole(d.per_question, *PER_QUESTION_RANGE),
                         "a whole number from {} to {}".format(*PER_QUESTION_RANGE)),
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


def check_schedule(raw: Mapping[str, object], strict: bool = False) -> ScheduleConfig:
    """`[schedule]` from its keys, defaults for the rest; raises ConfigError naming the bad one.
    A midday time equal to the tick's is refused when `strict` (a write); on load it turns
    the midday pass off, since a file written before it existed may set `time = "12:30"`."""
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
        if s.midday_time == s.time:
            clash = f"schedule.midday_time must differ from schedule.time ({s.time}): both would be due at once"
            if strict:
                raise ConfigError(clash)
            log.warning("%s; the midday pass is off until one of them changes", clash)
            s = replace(s, midday_time="")
    return replace(s, fill_times=_fill_times(s, strict))


def _fill_times(s: ScheduleConfig, strict: bool) -> tuple[str, ...]:
    """Sorted; one within `FILL_CLEARANCE_MIN` of the tick, the midday pass or an earlier fill
    time is refused when `strict`, else dropped with a warning."""
    fills = s.fill_times
    if not (isinstance(fills, (list, tuple)) and all(isinstance(t, str) for t in fills)):
        raise ConfigError(f'schedule.fill_times must be a list of 24-hour times like ["09:30", "15:15"] '
                          f'or [] (off), got {fills!r}')
    for t in fills:
        check_schedule_time(t, "fill_times", " in a list, or [] (off)")
    taken = {s.time: f"the tick ({s.time})", **({s.midday_time: f"the midday pass ({s.midday_time})"}
                                                 if s.midday_time else {})}
    kept: list[str] = []
    for t in sorted(fills):
        near = next((what for other, what in taken.items() if _minutes_apart(t, other) < FILL_CLEARANCE_MIN), None)
        if near is None:
            kept.append(t)
            taken[t] = f"the fill pass at {t}"
            continue
        clash = f"schedule.fill_times: {t} is within {FILL_CLEARANCE_MIN} minutes of {near}"
        if strict:
            raise ConfigError(clash)
        log.warning("%s; that fill pass is off until one of them changes", clash)
    return tuple(kept)


def _minutes_apart(a: str, b: str) -> int:
    """Round the clock, so 23:55 and 00:10 are 15 minutes apart."""
    diff = abs(_minutes(a) - _minutes(b))
    return min(diff, 24 * 60 - diff)


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


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


PROFILE_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _models(raw: object, strict: bool = False) -> tuple[dict[str, ModelProfile], bool, dict[str, str]]:
    """The profiles, whether claude_code may be named, and the slots."""
    if not isinstance(raw, dict):
        raise ConfigError("models must be a table of [models.<name>] sections")
    raw = dict(raw)
    slots = raw.pop(SLOTS_TABLE, {})
    allow = raw.pop(ALLOW_CLAUDE_CODE, False)
    if not isinstance(allow, bool):
        raise ConfigError(f"models.{ALLOW_CLAUDE_CODE} must be true or false, got {allow!r}")
    seen: dict[str, str] = {}
    for name in raw:
        check_profile_name(name)
        if name.lower() in seen:
            raise ConfigError(f"models: {seen[name.lower()]} and {name} differ only in case; rename one")
        seen[name.lower()] = name
    profiles = {name: check_profile(name, table, strict=False) for name, table in raw.items()}
    off = [n for n, p in profiles.items() if p.provider == "claude_code"] if not allow else []
    if off:
        # Kept, so the file still loads and the profile can be edited; refused when a model is built.
        log.warning("models %s: provider claude_code is off until %s = true is set under [models]",
                    ", ".join(off), ALLOW_CLAUDE_CODE)
    return profiles, allow, check_slots(slots, {DEFAULT_PROFILE, *profiles}, strict)


def check_slots(raw: object, profiles: set[str], strict: bool = True) -> dict[str, str]:
    where = f"models.{SLOTS_TABLE}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table of slot = \"profile\"")
    unknown = sorted(set(raw) - set(SLOTS))
    if unknown:
        raise ConfigError(f"unknown slot(s) in [{where}]: {', '.join(unknown)}; the slots are {', '.join(SLOTS)}")
    dangling = [slot for slot, profile in raw.items() if not (isinstance(profile, str) and profile in profiles)]
    for slot in dangling:
        message = f"{where}.{slot} names no profile {raw[slot]!r}; the profiles are {', '.join(sorted(profiles))}"
        if strict:
            raise ConfigError(message)
        log.warning("%s: until fixed, %s", message, "tick research uses the other slots" if slot == "ai_account"
                    else f"{slot} runs on {DEFAULT_PROFILE}")
    return {slot: raw[slot] for slot in SLOTS if slot in raw and slot not in dangling}


def check_profile_name(name: object) -> str:
    if not (isinstance(name, str) and PROFILE_NAME.fullmatch(name)):
        raise ConfigError(f"models: {name!r} is not a profile name (letters, digits, _ and -, up to 40)")
    if name.lower() == SLOTS_TABLE:
        raise ConfigError(f"models: {name!r} is reserved for [models.{SLOTS_TABLE}]; pick another profile name")
    if name.lower() == ALLOW_CLAUDE_CODE:
        raise ConfigError(f"models: {name} is a [models] setting, not a profile name")
    return name


def check_profile(name: str, raw: object, strict: bool = True) -> ModelProfile:
    """`[models.<name>]` as set (None where unset); raises ConfigError naming the bad key.
    Not `strict` (loading the file), a break of the base_url rules a profile may predate
    marks it `unusable` with a warning instead, so one old profile cannot stop the app."""
    where = f"models.{name}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")
    _reject_unknown(raw, ModelProfile, f"[{where}]")
    for needed in ("provider", "model"):
        if needed not in raw:
            raise ConfigError(f"{where} needs a {needed}")
    provider = raw["provider"]
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ConfigError(f"{where}.provider must be one of {', '.join(PROVIDERS)}, got {provider!r}")
    if "key" in raw and "key_env" in raw:
        raise ConfigError(f"{where} sets key and key_env; keep one")
    if not PROVIDERS[provider].keyed and ("key" in raw or "key_env" in raw):
        raise ConfigError(f"{where}: {provider} takes no key; remove key and key_env")
    if PROVIDERS[provider].base_url_required and "base_url" not in raw:
        raise ConfigError(f"{where} needs a base_url: the server's API root, like http://localhost:1234/v1")
    if provider in URLLESS_PROVIDERS and "base_url" in raw:
        raise ConfigError(f"{where}: {provider} reaches no endpoint; remove base_url")
    p = ModelProfile(**raw)
    if isinstance(p.claimed_cutoff, date) and not isinstance(p.claimed_cutoff, datetime):
        p = replace(p, claimed_cutoff=p.claimed_cutoff.isoformat())  # a bare TOML date
    low, high = PROFILE_CONTEXT_RANGE
    checks = {
        "model": (_text(p.model), "a model name like qwen3:8b"),
        "base_url": (p.base_url is None or isinstance(p.base_url, str) and re.match(r"https?://", p.base_url),
                     "an http(s) URL"),
        "context": (p.context is None or _whole(p.context, low, high), f"a whole number of tokens from {low} to {high}"),
        "timeout_s": (p.timeout_s is None or isinstance(p.timeout_s, (int, float)) and not isinstance(p.timeout_s, bool)
                      and p.timeout_s > 0, "a positive number"),
        "claimed_cutoff": (p.claimed_cutoff is None or _iso_day(p.claimed_cutoff), "a day like 2025-05-20"),
    }
    if provider == "claude_code":
        checks["model"] = (isinstance(p.model, str) and CLAUDE_CODE_MODEL.fullmatch(p.model) is not None,
                           "a model name the claude CLI takes, like sonnet, opus or claude-opus-5")
    for key, (ok, wanted) in checks.items():
        if not ok:
            raise ConfigError(f"{where}.{key} must be {wanted}, got {getattr(p, key)!r}")
    # Neither check echoes the value: it may be a key pasted into the wrong box.
    if p.key_env is not None and not _env_name(p.key_env):
        raise ConfigError(f"{where}.key_env must be the name of an environment variable like ANTHROPIC_API_KEY, "
                          "not a key; put a key itself in key")
    if p.key is not None and not _text(p.key):
        raise ConfigError(f"{where}.key must be a non-empty string")
    unusable = None
    if p.base_url and not _plain_url(p.base_url):
        # Never echoed: the user or query part may hold a password or key.
        unusable = f"{where}.base_url must be an http(s) URL with no user, password, query or fragment"
    elif (p.key or p.key_env or PROVIDERS[p.provider].fallback_key) and p.base_url and not _safe_for_keys(p.base_url):
        unusable = (f"{where} would send its key unencrypted to {urlsplit(p.base_url).hostname}: use https://, "
                    "or plain http:// only to this machine")
    if unusable and strict:
        raise ConfigError(unusable)
    if unusable:
        log.warning("model profile %s is unusable until fixed: %s", name, unusable)
    return replace(p, timeout_s=None if p.timeout_s is None else float(p.timeout_s),
                   key=p.key.strip() if p.key else None, unusable=unusable)


KEY_PREFIXES = ("sk-", "sk_", "gsk_", "ghp_", "xai-", "AIza", "AKIA")


def _env_name(value: object) -> bool:
    """A variable name, and nothing that looks like a key: long names mixing cases are keys."""
    if not (isinstance(value, str) and ENV_NAME.fullmatch(value)) or value.startswith(KEY_PREFIXES):
        return False
    mixed = value != value.upper() and value != value.lower()
    return len(value) <= 64 and not (mixed and len(value) > 24)


def _plain_url(value: object) -> bool:
    """An http(s) URL a request can be built on, with nothing in it that belongs in a key field."""
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and bool(parts.hostname) and "@" not in parts.netloc
            and not parts.query and not parts.fragment and "?" not in value and "#" not in value)


def _safe_for_keys(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https" or parts.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        return False


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _iso_day(value: object) -> bool:
    if not (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def claimed_cutoff(p: ModelProfile) -> date:
    """The profile's stated training cutoff, or the default."""
    return date.fromisoformat(p.claimed_cutoff) if p.claimed_cutoff else DEFAULT_CLAIMED_CUTOFF


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
