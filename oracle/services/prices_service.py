from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from oracle.config import PRICE_SOURCES
from oracle.market import registry
from oracle.market.bars import Bar
from oracle.market.fx import RateSeries
from oracle.market.sources import REFETCH_OVERLAP, AllFailed, Instrument, first_success
from oracle.net.client import RateLimitedClient
from oracle.services.context import AppContext
from oracle.services.errors import PriceUnavailable, UnknownSymbol
from oracle.services.fx_service import FxUpdate, update_fx
from oracle.services.http import open_client
from oracle.services.jobs_service import single_flight
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.fx import FxRepo
from oracle.store.health import HealthRepo, ListingHealth
from oracle.store.paper import PaperRepo
from oracle.store.universe import Resolved, UniverseRepo
from oracle.store.watchlists import WatchlistRepo

__all__ = [
    "Bar", "ConvertedBar", "ListingHealth", "ListingOutcome", "PriceUpdate", "SourceInfo", "WatchedUpdate",
    "benchmark_history_in_base", "day_change", "held_unwatched", "listing_health", "price_history",
    "price_history_in_base", "price_sources", "update_listing", "update_listings", "update_watched",
]

JOB = "prices_update"
LEASE_S = 1800.0
# Distinct days of "no data" from every source before a listing counts as dead. Days, not
# runs, so several updates in one day of a Yahoo hiccup never kill a listing.
DEAD_AFTER_DAYS = 3


@dataclass(frozen=True)
class ConvertedBar:
    bar: Bar
    rate: float | None
    """Units of the bar's currency per unit of base currency; None when no rate is
    stored for the day or the five before it."""
    close_base: float | None
    rate_source: str | None = None
    """Where the rate came from, for attribution; None for base-currency bars."""


@dataclass(frozen=True)
class SourceInfo:
    name: str
    position: int | None
    """1-based place in `[prices] sources`; None when left out."""
    enabled: bool
    reason: str | None
    """Why it is not used, when it is not."""


@dataclass(frozen=True)
class PriceUpdate:
    symbol: str
    added: int
    """Trading days new to the store."""
    last_date: date | None
    source: str


@dataclass(frozen=True)
class ListingOutcome:
    symbol: str
    added: int
    source: str | None
    error: str | None
    skipped: str | None = None
    """Why a dead listing was not tried; never an error."""


@dataclass(frozen=True)
class WatchedUpdate:
    listings: tuple[ListingOutcome, ...]
    fx: tuple[FxUpdate, ...]

    @property
    def failed(self) -> list[ListingOutcome]:
        return [o for o in self.listings if o.error is not None]


def held_unwatched(ctx: AppContext, watched: Sequence[str]) -> list[str]:
    """Symbols some paper account holds but that are not among `watched`, so a held
    listing is never dropped from pricing or analysis just because it left every list."""
    return [s for s in PaperRepo(ctx.conn).held_symbols() if s not in watched]


async def update_listing(ctx: AppContext, symbol: str, since: date | None = None) -> PriceUpdate:
    """Fetch new daily bars for one listing from the first configured source that has
    them. Nothing is stored unless a source succeeds. `since` refetches from that day,
    replacing what is stored, so the window's closes share one split and dividend basis."""
    async with open_client(ctx) as client:
        return await _update(ctx, client, symbol, since)


async def update_listings(ctx: AppContext, symbols: Sequence[str], since: date) -> list[ListingOutcome]:
    """Each listing refetched from `since` over one client; a dead listing is skipped with a
    note, a failure reported, and either way the rest carry on."""
    health = HealthRepo(ctx.conn)
    outcomes = []
    async with open_client(ctx) as client:
        for symbol in symbols:
            if (h := health.get(symbol)).dead:
                outcomes.append(ListingOutcome(symbol, 0, None, None, f"no source has data since {h.since}"))
                continue
            try:
                r = await _update(ctx, client, symbol, since)
            except (PriceUnavailable, UnknownSymbol) as exc:
                outcomes.append(ListingOutcome(symbol, 0, None, str(exc)))
            else:
                outcomes.append(ListingOutcome(r.symbol, r.added, r.source, None))
    return outcomes


async def update_watched(ctx: AppContext, fx: bool = True) -> WatchedUpdate:
    """Every watched listing, every listing a paper account holds and every configured
    benchmark once, then (with `fx`) the FX their currencies need, in one process at a time.
    Held listings are priced off every list too, so a stop can still sell them. A watched
    listing that fails is reported as a failure; a held one or a benchmark not in the
    universe is skipped with a note instead (nobody's watchlist entry gone missing). Either
    way the rest carry on."""
    with single_flight(ctx, JOB, LEASE_S) as lease:
        outcomes = []
        health = HealthRepo(ctx.conn)
        watched = WatchlistRepo(ctx.conn).all_symbols()
        universe = UniverseRepo(ctx.conn)
        held = held_unwatched(ctx, watched)
        benchmarks = [s for s in ctx.config.compare.benchmarks if s not in watched and s not in held]
        also_benchmark = set(held) & set(ctx.config.compare.benchmarks)
        unlisted = {s: ("held and a benchmark, but no longer in the universe: not priced" if s in also_benchmark
                        else "held, but no longer in the universe: not priced") for s in held}
        unlisted |= {s: "not in the universe: benchmark skipped" for s in benchmarks}
        async with open_client(ctx) as client:
            for symbol in watched + held + benchmarks:
                lease.check()
                if symbol in unlisted and universe.resolve(symbol) is None:
                    outcomes.append(ListingOutcome(symbol, 0, None, None, unlisted[symbol]))
                    continue
                if (h := health.get(symbol)).dead:
                    outcomes.append(ListingOutcome(symbol, 0, None, None, f"no source has data since {h.since}"))
                    continue
                try:
                    r = await _update(ctx, client, symbol)
                except (PriceUnavailable, UnknownSymbol) as exc:
                    outcomes.append(ListingOutcome(symbol, 0, None, str(exc)))
                else:
                    outcomes.append(ListingOutcome(r.symbol, r.added, r.source, None))
        lease.check()
        rates = await update_fx(ctx) if fx and any(o.skipped is None for o in outcomes) else []
    return WatchedUpdate(tuple(outcomes), tuple(rates))


def price_history(ctx: AppContext, symbol: str, days: int | None = None) -> list[Bar]:
    """Stored bars, oldest first; `days` keeps only the most recent trading days."""
    return BarRepo(ctx.conn).history(resolve_symbol(ctx, symbol).symbol, days)


def price_history_in_base(ctx: AppContext, symbol: str, days: int | None = None) -> list[ConvertedBar]:
    """Stored bars with each close converted to the base currency at that day's rate."""
    return _bars_in_base(ctx, price_history(ctx, symbol, days))


def benchmark_history_in_base(ctx: AppContext, symbol: str) -> list[ConvertedBar]:
    """A benchmark's stored bars converted to the base currency, read by symbol directly: a
    benchmark need not resolve in the universe, only have bars (see `update_watched`)."""
    return _bars_in_base(ctx, BarRepo(ctx.conn).history(symbol))


def _bars_in_base(ctx: AppContext, bars: Sequence[Bar]) -> list[ConvertedBar]:
    base = ctx.config.base_currency
    fx = FxRepo(ctx.conn, base)
    series: dict[str, RateSeries] = {}
    out = []
    for b in bars:
        if b.currency == base:
            out.append(ConvertedBar(b, 1.0, b.close))
            continue
        if b.currency not in series:
            series[b.currency] = RateSeries(fx.history(b.currency))
        picked = series[b.currency].pick(b.date)
        if picked is None:
            out.append(ConvertedBar(b, None, None))
        else:
            out.append(ConvertedBar(b, picked.units_per_base, b.close / picked.units_per_base, picked.source))
    return out


def day_change(recent: Sequence[ConvertedBar]) -> float | None:
    """The last close against the one before, as a ratio (0.05 is up 5%)."""
    return recent[-1].bar.close / recent[-2].bar.close - 1 if len(recent) >= 2 else None


def price_sources(ctx: AppContext) -> list[SourceInfo]:
    order = ctx.config.prices.sources
    out = []
    for name in PRICE_SOURCES:
        position = order.index(name) + 1 if name in order else None
        if position is None:
            reason = "not in [prices] sources"
        elif registry.missing_key(ctx.config.keys, name):
            reason = "no key"
        else:
            reason = None
        out.append(SourceInfo(name, position, reason is None, reason))
    return sorted(out, key=lambda s: (s.position is None, s.position or 0, s.name))


async def _update(ctx: AppContext, client: RateLimitedClient, symbol: str, since: date | None = None) -> PriceUpdate:
    listing = resolve_symbol(ctx, symbol)
    repo = BarRepo(ctx.conn)
    if since is None and (last := repo.last_date(listing.symbol)) is not None:
        since = last - REFETCH_OVERLAP
    instrument = Instrument(listing.symbol, listing.currency)
    now = ctx.clock.utcnow()
    try:
        bars, source = await first_success(
            registry.price_sources(ctx.config.prices.sources, client, ctx.config.keys),
            lambda s: s.daily_bars(instrument, since, now),
            # The window starts before the last stored bar, so a source still serving the
            # listing always returns at least that bar: empty means it has lost it.
            empty_is_no_data=True,
        )
    except AllFailed as exc:
        if exc.no_data:
            _note_no_data(ctx, listing, now.date())
        raise PriceUnavailable(f"no price data for {listing.symbol} ({exc})", exc.no_data) from None
    health = HealthRepo(ctx.conn)
    if health.clear(listing.symbol):
        UniverseRepo(ctx.conn).repick_primary(listing.company_key, health.dead_symbols())
    added = repo.upsert(bars, to_utc_iso(now))
    return PriceUpdate(listing.symbol, added, repo.last_date(listing.symbol), source)


def _note_no_data(ctx: AppContext, listing: Resolved, day: date) -> None:
    health = HealthRepo(ctx.conn)
    was_dead = health.get(listing.symbol).dead
    if health.note_no_data(listing.symbol, day, DEAD_AFTER_DAYS).dead and not was_dead:
        UniverseRepo(ctx.conn).repick_primary(listing.company_key, health.dead_symbols())


def listing_health(ctx: AppContext, symbol: str) -> ListingHealth:
    return HealthRepo(ctx.conn).get(resolve_symbol(ctx, symbol).symbol)
