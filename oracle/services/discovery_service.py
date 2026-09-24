"""Discovery: companies related to the watched ones, checked and added to the `discovered`
watchlist with why. The model only names companies seen in headlines; which listing a name
means, and whether it may be added, is decided here in code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from oracle.market.bars import is_stale
from oracle.net.client import RateLimitedClient
from oracle.research.llm import ChatModel, ModelError
from oracle.research.mentions import MENTIONS_SCHEMA, mentions_prompt, parse_mentions, same_company
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
    "DISCOVERED_LIST", "Added", "DiscoveredLine", "DiscoveryEvidence", "DiscoveryRun", "Skipped", "discover",
    "discovered", "gdelt_query", "move_discovered", "news_query", "remove_discovered",
]

JOB = "discovery"
LEASE_S = 1800.0
SOURCE_RELATED = "related"
# The user's rule, kept whatever `[discovery] never` says.
ALWAYS_NEVER = ("Tesla",)
# Bounds on one run's network and model use: seeds tried, price checks, peers per seed,
# headlines per model call (one call per seed).
MAX_SEEDS = 3
MAX_PRICE_CHECKS = 6
MAX_PEERS = 10
MAX_HEADLINES = 20
_LAST_SEED = "discovery_last_seed"

_WHY = {"supplier": "a supplier of", "customer": "a customer of", "competitor": "a competitor of",
        "partner": "a partner of"}


@dataclass(frozen=True)
class Added:
    symbol: str
    name: str
    seed: str
    relation: str
    why: str
    evidence: tuple[int, ...]


@dataclass(frozen=True)
class Skipped:
    name: str
    reason: str


@dataclass(frozen=True)
class DiscoveryRun:
    added: tuple[Added, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    seeds: tuple[str, ...] = ()
    """The watched listings it started from, in order."""
    source_errors: tuple[str, ...] = ()
    """Searches or model calls that failed; the run went on without them."""
    note: str | None = None
    """Why nothing was tried at all."""


@dataclass(frozen=True)
class DiscoveredLine(Discovery):
    why: str = ""


@dataclass(frozen=True)
class _Candidate:
    name: str
    relation: str
    seed: str
    evidence: tuple[int, ...] = ()
    symbol: str | None = None
    """Known already (a peer); else the name is looked up."""


def news_query(company: str) -> str:
    return f'"{blind_name(company)}" supplier OR customer OR competitor OR partner'


def gdelt_query(company: str) -> str:
    return f'"{blind_name(company)}"'


async def discover(ctx: AppContext, model: ChatModel | None = None) -> DiscoveryRun:
    """One run: up to `[discovery] per_tick` companies related to the watched ones, while the
    discovered list holds fewer than `max_total`. A failed search or model call is noted and
    the run carries on with what else it has."""
    cfg = ctx.config.discovery
    with single_flight(ctx, JOB, LEASE_S) as held:
        on_list = len(WatchlistRepo(ctx.conn).items(DISCOVERED_LIST))
        room = min(cfg.per_tick, cfg.max_total - on_list)
        if room <= 0:
            return DiscoveryRun(note=f"the discovered list is full ({on_list} of {cfg.max_total}): remove some, "
                                     "or raise the limit in Settings")
        seeds = _seeds(ctx)
        if not seeds:
            return DiscoveryRun(note="nothing watched yet: discovery starts from the companies you watch")
        model = model or configured_model(ctx)
        screen = _Screen(ctx, room)
        errors: list[str] = []
        used: list[str] = []
        async with open_client(ctx) as client:
            for seed in seeds[:MAX_SEEDS]:
                held.check()
                used.append(seed)
                MetaRepo(ctx.conn).set(_LAST_SEED, seed)
                profile = UniverseRepo(ctx.conn).profile(seed)
                news: list[_Candidate] = []
                if model is not None:
                    try:
                        news = await _from_news(ctx, client, model, profile, errors)
                    except ModelError as exc:
                        # A model that is down stays down for this run; waiting out its timeout per seed helps no one.
                        errors.append(f"the model, to read the news for {profile.symbol}: {exc}")
                        model = None
                candidates = news + _peers(ctx, seed, errors)
                for c in candidates:
                    if screen.done:
                        break
                    await screen.check(c)
                if screen.done:
                    break
        return DiscoveryRun(tuple(screen.added), tuple(screen.skipped), tuple(used), tuple(errors))


def discovered(ctx: AppContext) -> list[DiscoveredLine]:
    """What the discovered list holds, each with why it was found."""
    return [DiscoveredLine(**vars(d), why=_why(d.relation, d.seed) if d.source else "put on this list by hand")
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


def _why(relation: str, seed: str) -> str:
    """Why a company was discovered, in words."""
    if relation == "peer":
        return f"same industry as {seed}"
    if relation in _WHY:
        return f"named as {_WHY[relation]} {seed} in the news"
    return f"named beside {seed} in the news"


def _seeds(ctx: AppContext) -> list[str]:
    """Watched listings outside the discovered list, starting after the last run's seed."""
    repo = WatchlistRepo(ctx.conn)
    symbols = sorted({i.symbol for s in repo.summaries() if s.name.casefold() != DISCOVERED_LIST
                      for i in repo.items(s.name) if not i.missing})
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


async def _from_news(ctx: AppContext, client: RateLimitedClient, model: ChatModel, profile: ListingProfile,
                     errors: list[str]) -> list[_Candidate]:
    """Companies the seed's recent headlines name; raises ModelError when the model cannot answer."""
    found: dict[str, tuple[Article, str, str]] = {}
    for source, label, url, query, params, parse in (
            ("google_news", "Google News", GOOGLE_NEWS, news_query(profile.name), google_news_params,
             parse_google_news),
            ("gdelt", "GDELT", GDELT_DOC, gdelt_query(profile.name), gdelt_params, parse_gdelt)):
        try:
            articles = parse((await fetch(client, source, label, url, params=params(query))).text)
        except (ToolError, FeedUnreadable) as exc:
            errors.append(f"news for {profile.symbol} from {label}: {exc}")
            continue
        for a in articles:
            found.setdefault(a.url, (a, source, query))
    if not found:
        return []
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    newest = sorted(found.values(), key=lambda f: f[0].published or oldest, reverse=True)[:MAX_HEADLINES]
    repo, now = ArticleRepo(ctx.conn), _now(ctx)
    ids = [repo.save([a], source, now, query)[0] for a, source, query in newest]
    titles = [a.title for a, _, _ in newest]
    reply = await model.reply(mentions_prompt(profile.name, titles), MENTIONS_SCHEMA)
    return [_Candidate(m.name, m.relation, profile.symbol, tuple(ids[i] for i in m.headlines))
            for m in parse_mentions(reply, titles, profile.name)]


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
        self._repo.add(listing, profile.company_key, profile.name, SOURCE_RELATED, c.seed, c.relation, c.evidence,
                       _now(self._ctx))
        self._listed.add(profile.company_key)
        self.added.append(Added(listing, profile.name, c.seed, c.relation, _why(c.relation, c.seed), c.evidence))
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
