"""The index backfill: the members of each market's main indexes whose home line FinanceDatabase
lacks, added from Yahoo, and lines it has under the wrong code or far off in size, corrected.
Bounded per run and resumable: what one run leaves, the next picks up."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import chain, zip_longest

from oracle.market.fx import YahooFx
from oracle.market.sources import NoData, SourceFailed
from oracle.market.yahoo import PROFILE_MODULES, QuoteSummary, YahooProfile, parse_profile
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.services import universe_service
from oracle.services.context import AppContext
from oracle.services.http import open_client
from oracle.services.errors import JobAlreadyRunning
from oracle.services.jobs_service import Held, single_flight
from oracle.services.never_list import is_never, never_names
from oracle.store.backfill import CORRECTED, BackfillRepo
from oracle.store.health import HealthRepo
from oracle.store.universe import UniverseRepo
from oracle.universe import indexes as index_pages
from oracle.universe.backfill import (
    BACKFILL, inherit_classification, listing_from_profile, major_currency, usd_value,
)
from oracle.universe.indexes import INDEXES, IndexSpec, Member, Unreadable, page_url, parse_members
from oracle.universe.listings import (
    MARKET_CAP_BANDS, Listing, band_of_usd, is_receipt, is_receipt_symbol, normalise_name, place_line,
)

__all__ = ["ADDED_FROM_YAHOO", "ALREADY_THERE", "BackfillChange", "BackfillReport", "IndexCounts", "MAX_LOOKUPS", "add_from_yahoo",
           "backfill_status", "backfill_universe"]

MAX_LOOKUPS = 150
"""Yahoo profiles one run asks for: about five minutes at Yahoo's pace."""
LIST_MAX_AGE = timedelta(days=7)
PAGE_RETRY = timedelta(days=1)
"""How long a page that failed is left before it is asked for again."""
RETRY_AFTER = timedelta(days=30)
"""How long a member Yahoo could not add, or a size already checked, is left alone."""
# An index member this small is more likely a wrong row than a tiny company.
DOUBTFUL_BANDS = ("Micro Cap", "Nano Cap", None)
FAR_OFF = 2
"""Bands apart before a size counts as wrong."""
NEVER = "on the never list"
NOT_ON_YAHOO = "not on Yahoo"
CHECKED = "size checked"
FROM_YAHOO = "yahoo"
"""The index code of a line discovery found on Yahoo rather than in an index."""
ADDED_FROM_YAHOO = "added to the universe from Yahoo"
ALREADY_THERE = "already in the universe"
# Yahoo names OTC ADR lines ("...Y" tickers) without "ADR"; a home line is never one of these.
OTC_EXCHANGES = frozenset({"PNK", "OQX", "OQB"})


@dataclass(frozen=True)
class IndexCounts:
    code: str
    name: str
    members: int
    present: int
    """Members FinanceDatabase has itself."""
    added: int
    """Members the backfill added."""
    corrected: int
    """Lines corrected on this index's evidence."""
    failed: int
    """Members Yahoo could not add (or the never list keeps out)."""
    waiting: int
    """Members still to look up."""
    fetched_at: datetime | None
    error: str | None
    """Why the last read of its page failed; its cached list stands meanwhile."""


@dataclass(frozen=True)
class BackfillChange:
    symbol: str
    name: str
    index: str
    kind: str
    """added, corrected or failed."""
    note: str


@dataclass(frozen=True)
class BackfillReport:
    indexes: tuple[IndexCounts, ...]
    changes: tuple[BackfillChange, ...]
    """This run's."""
    lookups: int
    stopped: str | None
    """Why the run stopped early (Yahoo rate limiting it), when it did."""
    stale_corrections: tuple[str, ...] = ()
    """Sector corrections whose company key no company has after the regroup."""


async def backfill_universe(ctx: AppContext, on_progress: Callable[[str], None] = lambda _: None, *,
                            indexes: Sequence[IndexSpec] = INDEXES, max_lookups: int = MAX_LOOKUPS) -> BackfillReport:
    """Read each index's member list (cached a week), look up to `max_lookups` missing or doubtful
    members up on Yahoo, then lay what is stored over the universe. Holds the universe refresh's
    lock, so the two never interleave."""
    universe_service.require_loaded(ctx)
    with single_flight(ctx, universe_service.JOB, universe_service.LEASE_S) as held:
        run = _Run(ctx, held, on_progress, max_lookups)
        async with open_client(ctx) as client:
            await run.read_pages(client, indexes)
            await run.look_up(client, indexes)
        on_progress("saving")
        held.check()
        rebuild(ctx)
    return BackfillReport(tuple(backfill_status(ctx, indexes)), tuple(run.changes), run.lookups, run.stopped,
                          universe_service.stale_corrections(ctx))


async def add_from_yahoo(ctx: AppContext, symbol: str,
                         bands: Collection[str] | None = None) -> tuple[str | None, str]:
    """Add the line Yahoo lists as `symbol` to the universe at once, under the index backfill's
    guards and never over the counter, only in one of `bands` when given, in the company a
    regroup would give it, and kept by every refresh after. Returns (the symbol,
    ADDED_FROM_YAHOO), or (None, why not: ALREADY_THERE for a line the universe has)."""
    universe_service.require_loaded(ctx)
    if UniverseRepo(ctx.conn).resolve(symbol) is not None:
        return None, ALREADY_THERE
    now = ctx.clock.utcnow()
    async with open_client(ctx) as client:
        yahoo = _Yahoo(client, now)
        try:
            p = await yahoo.profile(symbol)
        except _Stop as exc:
            return None, str(exc)
        if isinstance(p, str):
            return None, p
        if why := refusal(p, never_names(ctx, user=True)):
            return None, why
        if p.exchange in OTC_EXCHANGES:
            return None, f"traded over the counter ({p.exchange})"
        line = await yahoo.line(p, UniverseRepo(ctx.conn).markets())
    if bands is not None and line.market_cap not in bands:
        return None, f"{line.market_cap or 'size unknown'} on Yahoo, too small to take on its ranking alone"
    try:
        with single_flight(ctx, universe_service.JOB, universe_service.LEASE_S):
            repo = UniverseRepo(ctx.conn)
            if repo.resolve(line.symbol) is not None:
                return None, ALREADY_THERE
            BackfillRepo(ctx.conn).add(line, FROM_YAHOO, ADDED_FROM_YAHOO, now)
            key = place_line(line, repo.grouping_rows())
            repo.add(inherit_classification([*repo.company_lines(key), replace(line, company_key=key)])[-1])
            repo.repick_primary(key, HealthRepo(ctx.conn).dead_symbols())
    except JobAlreadyRunning as exc:
        return None, str(exc)
    return line.symbol, ADDED_FROM_YAHOO


def refusal(p: YahooProfile, never) -> str | None:
    """Why Yahoo's line may not join the universe: only a company's own shares, never a fund,
    a depositary receipt or the never list."""
    return (f"not a company's shares ({p.quote_type or 'no type'})" if p.quote_type != "EQUITY" else
            "a depositary receipt" if is_receipt(p.name or "") or is_receipt_symbol(p.symbol) else
            NEVER if is_never(p.name or "", never) else None)


def rebuild(ctx: AppContext) -> None:
    """The stored universe regrouped with the backfill laid over FinanceDatabase's own lines."""
    repo = UniverseRepo(ctx.conn)
    repo.replace_all(universe_service.regroup(ctx, [x for x in repo.all_listings() if x.source is None]))


def backfill_status(ctx: AppContext, indexes: Sequence[IndexSpec] = INDEXES) -> list[IndexCounts]:
    """Each index's counts as they stand, without asking anything."""
    repo = BackfillRepo(ctx.conn)
    members = {spec.code: repo.members(spec.code) for spec in indexes}
    sources = UniverseRepo(ctx.conn).sources_of(m.symbol for ms in members.values() for m in ms)
    tries = repo.tries()
    corrected: dict[str, int] = {}
    for row in repo.rows():
        if row.kind == CORRECTED:
            corrected[row.index_code] = corrected.get(row.index_code, 0) + 1
    out = []
    for spec in indexes:
        keys = [m.symbol.casefold() for m in members[spec.code]]
        present = sum(k in sources and sources[k] is None for k in keys)
        added = sum(sources.get(k) == BACKFILL for k in keys)
        failed = sum(k not in sources and k in tries and tries[k].failed for k in keys)
        page = repo.page(spec.code)
        out.append(IndexCounts(spec.code, spec.name, len(keys), present, added, corrected.get(spec.code, 0), failed,
                               len(keys) - present - added - failed, page and page.fetched_at, page and page.error))
    return out


class _Stop(Exception):
    pass


class _Full(Exception):
    """The run's lookups cannot cover the next add and its wrong-code checks."""


class _Run:
    def __init__(self, ctx: AppContext, held: Held, on_progress: Callable[[str], None], max_lookups: int) -> None:
        self.ctx, self.held, self.on_progress, self.max_lookups = ctx, held, on_progress, max_lookups
        self.repo = BackfillRepo(ctx.conn)
        self.now = ctx.clock.utcnow()
        self.changes: list[BackfillChange] = []
        self.lookups = 0
        self.stopped: str | None = None

    async def read_pages(self, client: RateLimitedClient, indexes: Sequence[IndexSpec]) -> None:
        for i, spec in enumerate(indexes, 1):
            page = self.repo.page(spec.code)
            if page and ((page.fetched_at and self.now - page.fetched_at < LIST_MAX_AGE)
                         or (page.error and self.now - page.tried_at < PAGE_RETRY)):
                continue
            self.on_progress(f"reading index lists ({i}/{len(indexes)})")
            try:
                resp = await client.get(index_pages.SOURCE, page_url(spec),
                                        headers={"User-Agent": index_pages.USER_AGENT})
                if resp.status_code != 200:
                    raise Unreadable(f"{spec.name}: {page_url(spec)} answered HTTP {resp.status_code}")
                self.repo.replace_members(spec.code, parse_members(spec, resp.text), self.now)
            except (Unreadable, RequestFailed) as exc:
                self.repo.note_page_error(spec.code, str(exc), self.now)
            except CircuitOpen as exc:
                # Wikipedia is resting: every other index keeps its cached list.
                self.repo.note_page_error(spec.code, str(exc), self.now)
                return

    async def look_up(self, client: RateLimitedClient, indexes: Sequence[IndexSpec]) -> None:
        self.yahoo = _Yahoo(client, self.now)
        lines = UniverseRepo(self.ctx.conn).all_listings()
        self.markets = UniverseRepo(self.ctx.conn).markets()
        self.by_symbol = {x.symbol.casefold(): x for x in lines}
        self.by_name: dict[tuple, list[Listing]] = {}
        for x in lines:
            if x.source is None and not x.delisted:
                self.by_name.setdefault(_same_line(x), []).append(x)
        stored = {x.symbol.casefold() for x in self.repo.added()}
        tries = self.repo.tries()
        self.never = never = never_names(self.ctx, user=True)
        adds, checks = [], []
        for spec in indexes:
            to_add, to_check = [], []
            for m in self.repo.members(spec.code):
                key, tried = m.symbol.casefold(), tries.get(m.symbol.casefold())
                fresh = tried is not None and self.now - tried.tried_at < RETRY_AFTER
                if key in self.by_symbol:
                    x = self.by_symbol[key]
                    if x.source is None and x.market_cap in DOUBTFUL_BANDS and not fresh:
                        to_check.append((spec, x))
                elif key in stored:
                    continue
                elif is_never(m.name, never):
                    if tried is None:
                        self._fail(spec, m.symbol, m.name, NEVER)
                elif not (fresh and tried.failed):
                    to_add.append((spec, m))
            adds.append(to_add)
            checks.append(to_check)
        work = [*((self._add, *w) for w in _round_robin(adds)), *((self._check, *w) for w in _round_robin(checks))]
        for step, spec, item in work:
            if self.lookups >= self.max_lookups:
                return
            self.held.check()
            self.on_progress(f"looking up on Yahoo ({self.lookups + 1}/{min(len(work), self.max_lookups)})")
            try:
                await step(spec, item)
            except _Stop as exc:
                self.stopped = str(exc)
                return
            except _Full:
                return

    async def _add(self, spec: IndexSpec, m: Member) -> None:
        p = await self._profile(m.symbol)
        if isinstance(p, str):
            return self._fail(spec, m.symbol, m.name, p)
        if why := refusal(p, self.never):
            return self._fail(spec, m.symbol, p.name or m.name, why)
        line = await self.yahoo.line(p, self.markets)
        line = replace(line, isin=m.isin) if m.isin else line
        twins = [x for x in self.by_name.get(_same_line(line), []) if x.symbol.casefold() != line.symbol.casefold()]
        if self.lookups + len(twins) > self.max_lookups:
            # Stored only with its wrong-code checks, so the next run adds and checks it together.
            raise _Full
        note = f"in the {spec.name}; {line.market_cap or 'size unknown'}"
        self.repo.add(line, spec.code, note, self.now)
        self.changes.append(BackfillChange(line.symbol, line.name, spec.name, "added", note))
        self.by_symbol[line.symbol.casefold()] = line
        for x in twins:
            await self._wrong_code(spec, line, x)

    async def _wrong_code(self, spec: IndexSpec, line: Listing, x: Listing) -> None:
        """`x` carries `line`'s company name on its exchange under another code."""
        p = await self._profile(x.symbol)
        lists = f"the {spec.name} lists {line.name} as {line.symbol}"
        if p == NOT_ON_YAHOO:
            fields, note = {"delisted": True}, f"{p}; {lists}"
        elif isinstance(p, str):
            return
        elif p.name and normalise_name(p.name) != normalise_name(x.name):
            band = await self.yahoo.band(p)
            fields = {"name": p.name, **({"market_cap": band} if band else {})}
            note = f"Yahoo names it {p.name}; {lists}"
        else:
            return
        self._correct(spec, x, fields, note)

    async def _check(self, spec: IndexSpec, x: Listing) -> None:
        p = await self._profile(x.symbol)
        if not isinstance(p, str) and (band := await self.yahoo.band(p)):
            if _apart(band, x.market_cap) >= FAR_OFF:
                self._correct(spec, x, {"market_cap": band},
                              f"in the {spec.name}; Yahoo's market cap puts it in {band}, not {x.market_cap or 'no band'}")
        self.repo.note_try(x.symbol, spec.code, p if isinstance(p, str) else CHECKED, False, self.now)

    def _correct(self, spec: IndexSpec, x: Listing, fields: dict, note: str) -> None:
        self.repo.correct(x.symbol, fields, spec.code, note, self.now)
        self.changes.append(BackfillChange(x.symbol, x.name, spec.name, "corrected", note))

    def _fail(self, spec: IndexSpec, symbol: str, name: str, why: str) -> None:
        self.repo.note_try(symbol, spec.code, why, True, self.now)
        self.changes.append(BackfillChange(symbol, name, spec.name, "failed", why))

    async def _profile(self, symbol: str) -> YahooProfile | str:
        self.lookups += 1
        return await self.yahoo.profile(symbol)


class _Yahoo:
    """Yahoo profiles over one client, and the dollar rates that band them."""

    def __init__(self, client: RateLimitedClient, now: datetime) -> None:
        self.summary, self.fx, self.now = QuoteSummary(client), YahooFx(client), now
        self._rates: dict[str, float | None] = {}

    async def profile(self, symbol: str) -> YahooProfile | str:
        """The profile, or why there is none; _Stop when Yahoo refuses to answer at all."""
        try:
            return parse_profile(symbol, await self.summary.modules(symbol, PROFILE_MODULES))
        except NoData:
            return NOT_ON_YAHOO
        except SourceFailed as exc:
            raise _Stop(f"Yahoo stopped answering: {exc}") from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return "Yahoo's answer was unreadable"

    async def line(self, p: YahooProfile, markets: dict[str, str]) -> Listing:
        return listing_from_profile(p, markets.get(p.exchange or ""), await self._rate(p.currency))

    async def band(self, p: YahooProfile) -> str | None:
        usd = usd_value(p, await self._rate(p.currency))
        return band_of_usd(usd) if usd is not None else None

    async def _rate(self, currency: str | None) -> float | None:
        """Units of `currency`'s major unit per US dollar, or None when Yahoo has none."""
        major = major_currency(currency)
        if major is None or major == "USD":
            return 1.0 if major else None
        if major not in self._rates:
            try:
                rates = await self.fx.daily_rates("USD", major, (self.now - timedelta(days=10)).date(), self.now)
                self._rates[major] = rates[-1].units_per_base if rates else None
            except (NoData, SourceFailed, KeyError, IndexError, TypeError, ValueError):
                self._rates[major] = None
        return self._rates[major]


def _same_line(x: Listing) -> tuple:
    """Two live lines on one exchange with one company name are one company under two codes."""
    return normalise_name(x.name), x.country or "", x.exchange or ""


def _apart(band: str, other: str | None) -> int:
    if other not in MARKET_CAP_BANDS:
        return len(MARKET_CAP_BANDS)
    return abs(MARKET_CAP_BANDS.index(band) - MARKET_CAP_BANDS.index(other))


def _round_robin(per_index: list[list]) -> list:
    """One from each index in turn, so a run bounded short of the end still reaches every market."""
    return [w for w in chain.from_iterable(zip_longest(*per_index)) if w is not None]
