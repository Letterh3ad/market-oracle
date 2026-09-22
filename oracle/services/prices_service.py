from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from oracle.market.bars import Bar
from oracle.market.sources import NoData, PriceSource, SourceFailed
from oracle.market.yahoo import YahooPrices
from oracle.net.client import RateLimitedClient
from oracle.services.context import AppContext
from oracle.services.errors import PriceUnavailable
from oracle.services.http import open_client
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso

__all__ = ["Bar", "PriceUpdate", "price_history", "update_listing"]

# Re-fetched on every update, so a bar stored mid-session is replaced by the final one.
OVERLAP = timedelta(days=7)


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
        bars, source = await _first_with_data(ctx, client, listing.symbol, since)
    added = repo.upsert(bars, to_utc_iso(ctx.clock.utcnow()))
    return PriceUpdate(listing.symbol, added, repo.last_date(listing.symbol), source)


def price_history(ctx: AppContext, symbol: str, days: int | None = None) -> list[Bar]:
    """Stored bars, oldest first; `days` keeps only the most recent trading days."""
    return BarRepo(ctx.conn).history(resolve_symbol(ctx, symbol).symbol, days)


async def _first_with_data(ctx: AppContext, client: RateLimitedClient, symbol: str, since: date | None):
    failures = []
    for source in _sources(ctx, client):
        try:
            bars = await source.daily_bars(symbol, since, ctx.clock.utcnow())
        except (NoData, SourceFailed) as exc:
            failures.append(f"{source.name}: {exc}")
            continue
        if bars or since is not None:
            return bars, source.name
        failures.append(f"{source.name}: no data")
    raise PriceUnavailable(f"no price data for {symbol} ({'; '.join(failures)})")


def _sources(ctx: AppContext, client: RateLimitedClient) -> list[PriceSource]:
    available = {"yahoo": YahooPrices}
    return [available[name](client) for name in ctx.config.prices.sources]
