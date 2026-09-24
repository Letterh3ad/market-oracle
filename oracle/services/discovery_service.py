"""Discovery: companies related to the watched ones, in the news on the user's themes, or in
market-mover news, checked and added to the `discovered` watchlist with why. The model only
names companies seen in headlines; which listing a name means, and whether it may be added,
is decided here in code."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from oracle.market.bars import is_stale
from oracle.net.client import RateLimitedClient
from oracle.research.llm import ChatModel, ModelError
from oracle.research.mentions import (
    MENTIONS_SCHEMA, TOPIC_SCHEMA, Mention, mentions_prompt, parse_mentions, same_company, topic_prompt,
)
from oracle.research.news import (
    GDELT_DOC, GOOGLE_NEWS, Article, FeedUnreadable, gdelt_params, google_news_params, parse_gdelt,
    parse_google_news,
)
from oracle.research.tools import ToolError
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.http import open_client
from oracle.services.jobs_service import single_flight
from oracle.services.peers_service import choose_peers
from oracle.services.prices_service import update_listing
from oracle.services.research_service import configured_model
from oracle.services.research_tools.shelf import fetch
from oracle.services.universe_service import live_siblings, search_universe
from oracle.services.watch_service import DEFAULT_LIST, Watched, unwatch, watch
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso, transaction
from oracle.store.discoveries import DISCOVERED_LIST, Discovery, DiscoveryEvidence, DiscoveryRepo
from oracle.store.health import HealthRepo
from oracle.store.meta import MetaRepo
from oracle.store.universe import ListingProfile, UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.universe.listings import blind_name, is_receipt, normalise_name

__all__ = [
    "DISCOVERED_LIST", "SOURCES", "Added", "DiscoveredLine", "DiscoveryEvidence", "DiscoveryRun", "MoverSearch",
    "Skipped", "discover", "discovered", "gdelt_query", "move_discovered", "mover_query", "mover_searches",
    "news_query", "remove_discovered", "searched_sources", "theme_query",
]

JOB = "discovery"
LEASE_S = 1800.0
SOURCE_RELATED = "related"
SOURCE_THEME = "theme"
SOURCE_MOVER = "mover"
SOURCES = ("related", "themes", "movers")
"""In the order they take turns; each is switched by `[discovery] search_<name>`."""
# The user's rule, kept whatever `[discovery] never` says.
ALWAYS_NEVER = ("Tesla",)
# Bounds on one run's network and model use: seeds, themes and mover searches tried (one model
# call each), price checks, peers per seed, headlines per model call.
MAX_SEEDS = 3
MAX_THEMES = 2
MAX_MOVER_SEARCHES = 2
MAX_PRICE_CHECKS = 6
MAX_PEERS = 10
MAX_HEADLINES = 20
_LAST_SEED = "discovery_last_seed"
_LAST_SOURCE = "discovery_last_source"
_LAST_THEME = "discovery_last_theme"
_LAST_MOVER = "discovery_last_mover"

_WHY = {"supplier": "a supplier of", "customer": "a customer of", "competitor": "a competitor of",
        "partner": "a partner of"}

# kind -> (words for the user, search terms). One OR group in brackets reads the same in
# Google News and GDELT.
_MOVER_KINDS = {
    "movers": ("the biggest risers and fallers", '("biggest gainers" OR "biggest losers" OR "top gainers" '
                                                 'OR "top losers" OR "shares soar" OR "shares plunge")'),
    "volume": ("unusual trading volume", '("unusual volume" OR "heavy volume" OR "volume spike" OR "heavy trading")'),
    "results": ("results surprises", '("beat estimates" OR "missed estimates" OR "profit warning" '
                                     'OR "earnings surprise" OR "results beat")'),
}


@dataclass(frozen=True)
class Added:
    symbol: str
    name: str
    seed: str
    relation: str
    why: str
    evidence: tuple[int, ...]
    source: str = SOURCE_RELATED


@dataclass(frozen=True)
class Skipped:
    name: str
    reason: str


@dataclass(frozen=True)
class DiscoveryRun:
    added: tuple[Added, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    seeds: tuple[str, ...] = ()
    """What it searched from, in order: watched listings, themes, mover searches."""
    source_errors: tuple[str, ...] = ()
    """Searches or model calls that failed; the run went on without them."""
    note: str | None = None
    """Why nothing was tried at all."""
    sources: tuple[str, ...] = ()
    """The sources tried, in order: the first had the turn, the rest took it over."""


@dataclass(frozen=True)
class DiscoveredLine(Discovery):
    why: str = ""


@dataclass(frozen=True)
class MoverSearch:
    kind: str
    region: str

    @property
    def label(self) -> str:
        return f"{_MOVER_KINDS[self.kind][0]} ({self.region})"


@dataclass(frozen=True)
class _Candidate:
    name: str
    relation: str
    seed: str
    evidence: tuple[int, ...] = ()
    symbol: str | None = None
    """Known already (a peer); else the name is looked up."""
    source: str = SOURCE_RELATED


def news_query(company: str) -> str:
    return f'"{blind_name(company)}" supplier OR customer OR competitor OR partner'


def gdelt_query(company: str) -> str:
    return f'"{blind_name(company)}"'


def theme_query(theme: str) -> str:
    return f'{_phrase(theme)} shares OR stocks OR company'


def _phrase(text: str) -> str:
    return '"' + text.replace('"', " ").strip() + '"'


def mover_query(search: MoverSearch) -> str:
    return f'{_MOVER_KINDS[search.kind][1]} "{search.region}"'


def searched_sources(ctx: AppContext) -> list[str]:
    """The sources switched on; themes only with some themes named."""
    cfg = ctx.config.discovery
    return [s for s in SOURCES if getattr(cfg, f"search_{s}") and (s != "themes" or cfg.themes)]


def mover_searches(ctx: AppContext) -> list[MoverSearch]:
    """Each kind of mover news in each country the watched listings are in, most watched first."""
    universe = UniverseRepo(ctx.conn)
    counts: dict[str, int] = {}
    for symbol in _watched_symbols(ctx):
        profile = universe.profile(symbol)
        if profile and profile.country:
            counts[profile.country] = counts.get(profile.country, 0) + 1
    regions = sorted(counts, key=lambda c: (-counts[c], c))
    return [MoverSearch(kind, region) for kind in _MOVER_KINDS for region in regions]


async def discover(ctx: AppContext, model: ChatModel | None = None) -> DiscoveryRun:
    """One run: up to `[discovery] per_tick` companies, while the discovered list holds fewer
    than `max_total`. The sources take turns across runs; one that fails or finds nothing to
    add hands the run to the next. A failed search or model call is noted, never fatal."""
    cfg = ctx.config.discovery
    with single_flight(ctx, JOB, LEASE_S) as held:
        on_list = len(WatchlistRepo(ctx.conn).items(DISCOVERED_LIST))
        room = min(cfg.per_tick, cfg.max_total - on_list)
        if room <= 0:
            return DiscoveryRun(note=f"the discovered list is full ({on_list} of {cfg.max_total}): remove some, "
                                     "or raise the limit in Settings")
        seeds = _seeds(ctx)
        # Related and mover searches both start from what is watched; themes need nothing watched.
        usable = [s for s in searched_sources(ctx) if s == "themes" or seeds]
        if not usable:
            return DiscoveryRun(note=_nothing_to_search(ctx, seeds))
        meta = MetaRepo(ctx.conn)
        order = [s for s in _after(list(SOURCES), meta.get(_LAST_SOURCE)) if s in usable]
        meta.set(_LAST_SOURCE, order[0])
        run = _Run(ctx, held, model or configured_model(ctx), _Screen(ctx, room))
        tried: list[str] = []
        async with open_client(ctx) as client:
            run.client = client
            for source in order:
                tried.append(source)
                await {"related": _related, "themes": _themes, "movers": _movers}[source](run, seeds)
                if run.screen.done:
                    break
        return DiscoveryRun(tuple(run.screen.added), tuple(run.screen.skipped), tuple(run.used), tuple(run.errors),
                            sources=tuple(tried))


def discovered(ctx: AppContext) -> list[DiscoveredLine]:
    """What the discovered list holds, each with why it was found."""
    return [DiscoveredLine(**vars(d), why=_why(d.source, d.relation, d.seed) if d.source else "put on this list by hand")
            for d in DiscoveryRepo(ctx.conn).on_list()]


def remove_discovered(ctx: AppContext, symbol: str) -> bool:
    """Off the discovered list, and never added again."""
    return unwatch(ctx, symbol, DISCOVERED_LIST)


def move_discovered(ctx: AppContext, symbol: str) -> Watched:
    """Onto the user's own list and off the discovered one; its reasons are kept."""
    with transaction(ctx.conn):
        moved = watch(ctx, symbol, DEFAULT_LIST)
        WatchlistRepo(ctx.conn).remove(DISCOVERED_LIST, moved.symbol)
        DiscoveryRepo(ctx.conn).mark_moved(moved.symbol, _now(ctx))
    return moved


def _why(source: str, relation: str, seed: str) -> str:
    """Why a company was discovered, in words."""
    if source == SOURCE_THEME:
        return f'named in the news on your theme "{seed}"'
    if source == SOURCE_MOVER:
        return f"named in news of {seed}"
    if relation == "peer":
        return f"same industry as {seed}"
    if relation in _WHY:
        return f"named as {_WHY[relation]} {seed} in the news"
    return f"named beside {seed} in the news"


def _nothing_to_search(ctx: AppContext, seeds: list[str]) -> str:
    cfg = ctx.config.discovery
    if not any(getattr(cfg, f"search_{s}") for s in SOURCES):
        return "every source is off in Settings"
    if seeds:
        return "only the theme source is on, and Settings names no themes"
    return "nothing watched yet: discovery starts from the companies you watch (or themes named in Settings)"


def _watched_symbols(ctx: AppContext) -> list[str]:
    """Watched listings outside the discovered list."""
    repo = WatchlistRepo(ctx.conn)
    return sorted({i.symbol for s in repo.summaries() if s.name.casefold() != DISCOVERED_LIST
                   for i in repo.items(s.name) if not i.missing})


def _after(items: list, last: str | None, key: Callable = str) -> list:
    """`items` starting with the one after `last` (by `key`), or from the top without it."""
    keys = [key(i) for i in items]
    if last not in keys:
        return items
    at = keys.index(last) + 1
    return items[at:] + items[:at]


def _seeds(ctx: AppContext) -> list[str]:
    """Watched listings outside the discovered list, starting after the last run's seed."""
    symbols = _watched_symbols(ctx)
    last = MetaRepo(ctx.conn).get(_LAST_SEED)
    start = next((n for n, s in enumerate(symbols) if s > last), 0) if last else 0
    return symbols[start:] + symbols[:start]


def _peers(ctx: AppContext, seed: str, errors: list[str]) -> list[_Candidate]:
    try:
        rows = choose_peers(ctx, seed).rows
    except ServiceError as exc:
        errors.append(f"peers of {seed}: {exc}")
        return []
    return [_Candidate(r.name, "peer", seed, symbol=r.symbol) for r in rows[:MAX_PEERS]]


def _feeds(google_query: str, gdelt: str) -> tuple:
    return (("google_news", "Google News", GOOGLE_NEWS, google_query, google_news_params, parse_google_news),
            ("gdelt", "GDELT", GDELT_DOC, gdelt, gdelt_params, parse_gdelt))


async def _related(run: _Run, seeds: list[str]) -> None:
    for seed in seeds[:MAX_SEEDS]:
        run.start(seed)
        MetaRepo(run.ctx.conn).set(_LAST_SEED, seed)
        profile = UniverseRepo(run.ctx.conn).profile(seed)
        named = await run.named(profile.symbol, _feeds(news_query(profile.name), gdelt_query(profile.name)),
                                lambda titles: mentions_prompt(profile.name, titles), MENTIONS_SCHEMA, profile.name)
        news = [_Candidate(m.name, m.relation, profile.symbol, ids) for m, ids in named]
        if await run.screen_all(news + _peers(run.ctx, seed, run.errors)):
            return


async def _themes(run: _Run, _seeds: list[str]) -> None:
    meta = MetaRepo(run.ctx.conn)
    for theme in _after(list(run.ctx.config.discovery.themes), meta.get(_LAST_THEME))[:MAX_THEMES]:
        run.start(theme)
        meta.set(_LAST_THEME, theme)
        named = await run.named(f'the theme "{theme}"', _feeds(theme_query(theme), _phrase(theme)),
                                lambda titles: topic_prompt(theme, titles), TOPIC_SCHEMA)
        if await run.screen_all([_Candidate(m.name, "theme", theme, ids, source=SOURCE_THEME) for m, ids in named]):
            return


async def _movers(run: _Run, _seeds: list[str]) -> None:
    meta = MetaRepo(run.ctx.conn)
    searches = _after(mover_searches(run.ctx), meta.get(_LAST_MOVER), key=lambda s: s.label)
    for search in searches[:MAX_MOVER_SEARCHES]:
        run.start(search.label)
        meta.set(_LAST_MOVER, search.label)
        query = mover_query(search)
        named = await run.named(search.label, _feeds(query, query),
                                lambda titles: topic_prompt(search.label, titles), TOPIC_SCHEMA)
        if await run.screen_all([_Candidate(m.name, search.kind, search.label, ids, source=SOURCE_MOVER)
                                 for m, ids in named]):
            return


class _Run:
    """What one run's sources share: the model (dropped once it fails), the screen, and what
    was searched and what failed."""

    def __init__(self, ctx: AppContext, held, model: ChatModel | None, screen: _Screen) -> None:
        self.ctx = ctx
        self.held = held
        self.model = model
        self.screen = screen
        self.client: RateLimitedClient | None = None
        self.errors: list[str] = []
        self.used: list[str] = []

    def start(self, seed: str) -> None:
        self.held.check()
        self.used.append(seed)

    async def screen_all(self, candidates: list[_Candidate]) -> bool:
        """Checks candidates until the run is done; says whether it is."""
        for c in candidates:
            if self.screen.done:
                break
            await self.screen.check(c)
        return self.screen.done

    async def named(self, what: str, feeds: tuple, messages: Callable[[list[str]], list[dict]], schema: dict,
                    seed: str | None = None) -> list[tuple[Mention, tuple[int, ...]]]:
        """Companies the feeds' newest headlines name, each with the article ids naming it.
        None without a working model, or when no headlines came back."""
        if self.model is None:
            return []
        found: dict[str, tuple[Article, str, str]] = {}
        for source, label, url, query, params, parse in feeds:
            try:
                articles = parse((await fetch(self.client, source, label, url, params=params(query))).text)
            except (ToolError, FeedUnreadable) as exc:
                self.errors.append(f"news for {what} from {label}: {exc}")
                continue
            for a in articles:
                found.setdefault(a.url, (a, source, query))
        if not found:
            return []
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        newest = sorted(found.values(), key=lambda f: f[0].published or oldest, reverse=True)[:MAX_HEADLINES]
        repo, now = ArticleRepo(self.ctx.conn), _now(self.ctx)
        ids = [repo.save([a], source, now, query)[0] for a, source, query in newest]
        titles = [a.title for a, _, _ in newest]
        try:
            reply = await self.model.reply(messages(titles), schema)
        except ModelError as exc:
            # A model that is down stays down for this run; waiting out its timeout per search helps no one.
            self.errors.append(f"the model, to read the news for {what}: {exc}")
            self.model = None
            return []
        return [(m, tuple(ids[i] for i in m.headlines)) for m in parse_mentions(reply, titles, seed)]


class _Screen:
    """Checks candidates in turn and adds those that pass, until `room` are added or the
    price checks run out."""

    def __init__(self, ctx: AppContext, room: int) -> None:
        self._ctx = ctx
        self._room = room
        self._checks = 0
        self._universe = UniverseRepo(ctx.conn)
        self._repo = DiscoveryRepo(ctx.conn)
        self._never = [normalise_name(n) for n in (*ALWAYS_NEVER, *ctx.config.discovery.never)]
        self._removed_symbols, self._removed_keys = self._repo.removed()
        lists = WatchlistRepo(ctx.conn)
        self._watched = self._keys(s for w in lists.summaries() if w.name.casefold() != DISCOVERED_LIST
                                   for s in (i.symbol for i in lists.items(w.name)))
        self._listed = self._keys(i.symbol for i in lists.items(DISCOVERED_LIST))
        self._seen: set[str] = set()
        self.added: list[Added] = []
        self.skipped: list[Skipped] = []

    @property
    def done(self) -> bool:
        return len(self.added) >= self._room or self._checks >= MAX_PRICE_CHECKS

    async def check(self, c: _Candidate) -> None:
        if self._is_never(c.name):
            return self._skip(c, "on the never list")
        profile = self._universe.profile(c.symbol) if c.symbol else self._lookup(c.name)
        if profile is None:
            return self._skip(c, "not in the universe under that name")
        if profile.company_key in self._seen:
            return None
        self._seen.add(profile.company_key)
        if profile.type != "equity" or is_receipt(profile.name):
            return self._skip(c, "not a company's own shares")
        names = [profile.name, *(x.name for x in self._universe.company_listings(profile.company_key))]
        if any(self._is_never(n) for n in names):
            return self._skip(c, "on the never list")
        if profile.company_key in self._watched:
            return self._skip(c, "already watched")
        if profile.company_key in self._listed:
            return self._skip(c, "already discovered")
        symbols = {s.upper() for s in self._universe.company_symbols(profile.company_key)}
        if profile.company_key in self._removed_keys or symbols & self._removed_symbols:
            return self._skip(c, "you removed it from the discovered list")
        listing = self._live_line(profile)
        if listing is None:
            return self._skip(c, "no listing with live prices")
        self._checks += 1
        try:
            update = await update_listing(self._ctx, listing)
        except ServiceError as exc:
            return self._skip(c, f"no price data ({exc})")
        today = self._ctx.clock.utcnow().date()
        if update.last_date is None or is_stale(update.last_date, today):
            return self._skip(c, f"its prices are out of date (last {update.last_date})")
        self._repo.add(listing, profile.company_key, profile.name, c.source, c.seed, c.relation, c.evidence,
                       _now(self._ctx))
        self._listed.add(profile.company_key)
        self.added.append(Added(listing, profile.name, c.seed, c.relation, _why(c.source, c.relation, c.seed),
                                c.evidence, c.source))
        return None

    def _lookup(self, name: str) -> ListingProfile | None:
        """The company the name means: one of its listings named exactly that (legal forms
        aside), else one whose name starts with it, biggest first as the search ranks them."""
        want = blind_name(name)
        hits = search_universe(self._ctx, want, filters={"type": ["equity"]}, limit=10)
        names = {h.symbol: {blind_name(h.name)} | {blind_name(x.name) for x in
                                                   self._universe.company_listings(h.company_key)} for h in hits}
        exact = [h for h in hits if want in names[h.symbol]]
        leading = [h for h in hits if any(same_company(n, want) for n in names[h.symbol])]
        best = (exact or leading or [None])[0]
        return self._universe.profile(best.symbol) if best else None

    def _live_line(self, profile: ListingProfile) -> str | None:
        """The primary line (home or most liquid), else its best live sibling."""
        primary = profile.primary_symbol
        if not HealthRepo(self._ctx.conn).get(primary).dead:
            return primary
        siblings = live_siblings(self._ctx, primary)
        return siblings[0].symbol if siblings else None

    def _is_never(self, name: str) -> bool:
        text = f" {normalise_name(name)} "
        return any(n and f" {n} " in text for n in self._never)

    def _keys(self, symbols) -> set[str]:
        return {r.company_key for s in symbols if (r := self._universe.resolve(s))}

    def _skip(self, c: _Candidate, reason: str) -> None:
        self.skipped.append(Skipped(c.name, reason))


def _now(ctx: AppContext) -> str:
    return to_utc_iso(ctx.clock.utcnow())
