"""Sequences that span services, shared by the CLI and the GUI. `oracle tick` (phase 10)
will own the full run: prices, FX, fills, analysis, strategy step."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.services.context import AppContext
from oracle.services.errors import PriceUnavailable
from oracle.services.fx_service import update_fx
from oracle.services.paper_service import FillOutcome, fill_pending
from oracle.services.prices_service import ListingOutcome, WatchedUpdate, price_history, update_listing, update_watched

__all__ = ["PricesRun", "update_listing_prices", "update_prices"]


@dataclass(frozen=True)
class PricesRun:
    update: WatchedUpdate
    fills: tuple[FillOutcome, ...]


async def update_prices(ctx: AppContext) -> PricesRun:
    """Every watched listing and the FX it needs, then any paper orders the new bars
    complete (orders fill at the next open)."""
    update = await update_watched(ctx)
    return PricesRun(update, tuple(fill_pending(ctx)))


async def update_listing_prices(ctx: AppContext, symbol: str) -> PricesRun:
    """One listing, watched or not, then the FX its currency needs and any fills."""
    try:
        r = await update_listing(ctx, symbol)
    except PriceUnavailable as exc:
        return PricesRun(WatchedUpdate((ListingOutcome(symbol.upper(), 0, None, str(exc)),), ()), ())
    currency = price_history(ctx, r.symbol, days=1)[-1].currency
    fx = [] if currency == ctx.config.base_currency else await update_fx(ctx, currencies=[currency])
    update = WatchedUpdate((ListingOutcome(r.symbol, r.added, r.source, None),), tuple(fx))
    return PricesRun(update, tuple(fill_pending(ctx)))
