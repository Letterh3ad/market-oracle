"""Sequences that span services, shared by the CLI and the GUI. `oracle tick` (phase 10)
will own the full run: prices, FX, fills, analysis, strategy step."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.services.context import AppContext
from oracle.services.paper_service import FillOutcome, fill_pending
from oracle.services.prices_service import WatchedUpdate, update_watched

__all__ = ["PricesRun", "update_prices"]


@dataclass(frozen=True)
class PricesRun:
    update: WatchedUpdate
    fills: tuple[FillOutcome, ...]


async def update_prices(ctx: AppContext) -> PricesRun:
    """Every watched listing and the FX it needs, then any paper orders the new bars
    complete (orders fill at the next open)."""
    update = await update_watched(ctx)
    return PricesRun(update, tuple(fill_pending(ctx)))
