"""Buttons that run work now. Each runs inside its request under the job lock: the server
keeps answering other requests while it awaits the network, and a job already running
anywhere answers 409 straight away."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.analysis_service import WatchedAnalysis, analyse_listing, analyse_watched
from oracle.services.context import AppContext
from oracle.services.research_service import research as run_research
from oracle.services.routine_service import update_listing_prices, update_prices
from oracle.services.tick_service import run_tick

router = APIRouter(prefix="/api/jobs")


@router.post("/prices")
async def prices(symbol: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every watched listing, or just `symbol` (watched or not)."""
    run = await (update_listing_prices(ctx, symbol) if symbol else update_prices(ctx))
    return {
        "listings": [asdict(o) for o in run.update.listings],
        "fx": [asdict(f) for f in run.update.fx],
        "fills": [shapes.order_outcome(f.order, f.status, f.note) for f in run.fills],
    }


@router.post("/analyse")
async def analyse(symbol: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every watched listing, or just `symbol` (watched or not)."""
    if symbol:
        result = await analyse_listing(ctx, symbol)
        runs = [WatchedAnalysis(result.symbol, result, None)]
    else:
        runs = await analyse_watched(ctx)
    return {"listings": [
        {"symbol": r.symbol, "error": r.error,
         "lean": None if r.analysis is None or r.analysis.lean is None
         else {"label": r.analysis.lean.label, "probability": r.analysis.lean.probability}}
        for r in runs
    ]}


@router.post("/research")
async def research(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """One research run with its report; about a minute on the default local model."""
    return shapes.research_run(await run_research(ctx, symbol))


@router.post("/tick")
async def tick(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The whole routine once: prices, FX, fills, analysis, strategy step."""
    return shapes.tick(await run_tick(ctx))
