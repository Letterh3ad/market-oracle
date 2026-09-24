"""Kept backtest results, and the days a list's prices cover. A backtest starts as a job
(`POST /api/jobs/backtest`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.backtest_service import KEEP, kept_backtest, kept_backtests, stored_span
from oracle.services.context import AppContext
from oracle.services.watch_service import DEFAULT_LIST

router = APIRouter(prefix="/api/backtests")


@router.get("")
async def runs(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The last few results, newest first, without their chart points."""
    return {"runs": [shapes.backtest_summary(k) for k in kept_backtests(ctx)], "keep": KEEP}


@router.get("/span")
async def span(watchlist: str = DEFAULT_LIST, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The first and last day any of the list's listings has a stored price (null with none)."""
    name, first, last = stored_span(ctx, watchlist)
    return {"watchlist": name, "first": first, "last": last, "base_currency": ctx.config.base_currency}


@router.get("/{run_id}")
async def run(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    return shapes.backtest(kept_backtest(ctx, run_id))
