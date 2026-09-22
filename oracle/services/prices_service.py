from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from oracle.config import PRICE_SOURCES
from oracle.market.bars import Bar
from oracle.market.eodhd import EodhdPrices
from oracle.market.fx import pick_rate
from oracle.market.sources import Instrument, NoData, PriceSource, SourceFailed
from oracle.market.yahoo import YahooPrices
from oracle.net.client import RateLimitedClient
from oracle.services.context import AppContext
from oracle.services.errors import PriceUnavailable
from oracle.services.http import open_client
from oracle.services.sources import missing_key
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.fx import FxRepo
from oracle.store.db import to_utc_iso

__all__ = [
    "Bar", "ConvertedBar", "PriceUpdate", "SourceInfo",
    "price_history", "price_history_in_base", "price_sources", "update_listing",
]

# Re-fetched on every update, so a bar stored mid-session is replaced by the final one.
OVERLAP = timedelta(days=7)


@dataclass(frozen=True)
class ConvertedBar:
    bar: Bar
    rate: float | None
    """Units of the bar's currency per unit of base currency; None when no rate is
    stored for the day or the five before it."""
    close_base: float | None


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


async def update_listing(ctx: AppContext, symbol: str) -> PriceUpdate:
    """Fetch new daily bars for one listing from the first configured source that has
    them. Nothing is stored unless a source succeeds."""
    listing = resolve_symbol(ctx, symbol)
    repo = BarRepo(ctx.conn)
    last = repo.last_date(listing.symbol)
    since = None if last is None else last - OVERLAP
    async with open_client(ctx) as client:
        bars, source = await _first_with_data(ctx, client, Instrument(listing.symbol, listing.currency), since)
    added = repo.upsert(bars, to_utc_iso(ctx.clock.utcnow()))
    return PriceUpdate(listing.symbol, added, repo.last_date(listing.symbol), source)


def price_history(ctx: AppContext, symbol: str, days: int | None = None) -> list[Bar]:
    """Stored bars, oldest first; `days` keeps only the most recent trading days."""
    return BarRepo(ctx.conn).history(resolve_symbol(ctx, symbol).symbol, days)


def price_history_in_base(ctx: AppContext, symbol: str, days: int | None = None) -> list[ConvertedBar]:
    """Stored bars with each close converted to the base currency at that day's rate."""
    bars = price_history(ctx, symbol, days)
    base = ctx.config.base_currency
    fx = FxRepo(ctx.conn, base)
    series: dict[str, list] = {}
    out = []
    for b in bars:
        if b.currency == base:
            out.append(ConvertedBar(b, 1.0, b.close))
            continue
        rates = series.setdefault(b.currency, fx.history(b.currency))
        picked = pick_rate(rates, b.date)
        rate = picked.units_per_base if picked else None
        out.append(ConvertedBar(b, rate, b.close / rate if rate else None))
    return out


def price_sources(ctx: AppContext) -> list[SourceInfo]:
    order = ctx.config.prices.sources
    out = []
    for name in PRICE_SOURCES:
        position = order.index(name) + 1 if name in order else None
        reason = "not in [prices] sources" if position is None else "no key" if missing_key(ctx, name) else None
        out.append(SourceInfo(name, position, reason is None, reason))
    return sorted(out, key=lambda s: (s.position is None, s.position or 0, s.name))


async def _first_with_data(ctx: AppContext, client: RateLimitedClient, instrument: Instrument, since: date | None):
    failures = []
    for source in _sources(ctx, client):
        try:
            bars = await source.daily_bars(instrument, since, ctx.clock.utcnow())
        except (NoData, SourceFailed) as exc:
            failures.append(f"{source.name}: {exc}")
            continue
        if bars or since is not None:
            return bars, source.name
        failures.append(f"{source.name}: no data")
    raise PriceUnavailable(f"no price data for {instrument.symbol} ({'; '.join(failures)})")


def _sources(ctx: AppContext, client: RateLimitedClient) -> list[PriceSource]:
    enabled = {s.name for s in price_sources(ctx) if s.enabled}
    build = {
        "yahoo": lambda: YahooPrices(client),
        "eodhd": lambda: EodhdPrices(client, ctx.config.keys.get("eodhd")),
    }
    return [build[name]() for name in ctx.config.prices.sources if name in enabled]
