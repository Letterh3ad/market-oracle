"""Buttons that run work now. Each runs inside its request under the job lock: the server
keeps answering other requests while it awaits the network, and a job already running
anywhere answers 409 straight away."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.services.analysis_service import WatchedAnalysis, analyse_watched
from oracle.services.analysis_service import analyse as analyse_one
from oracle.services.context import AppContext
from oracle.services.routine_service import update_listing_prices, update_prices

router = APIRouter(prefix="/api/jobs")


@router.post("/prices")
async def prices(symbol: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every watched listing, or just `symbol` (watched or not)."""
    run = await (update_listing_prices(ctx, symbol) if symbol else update_prices(ctx))
    return {
        "listings": [asdict(o) for o in run.update.listings],
        "fx": [asdict(f) for f in run.update.fx],
        "fills": [{"symbol": f.order.symbol, "side": f.order.side, "status": f.status, "note": f.note}
                  for f in run.fills],
    }


@router.post("/analyse")
async def analyse(symbol: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every watched listing, or just `symbol` (watched or not)."""
    if symbol:
        result = await analyse_one(ctx, symbol)
        runs = [WatchedAnalysis(result.symbol, result, None)]
    else:
        runs = await analyse_watched(ctx)
    return {"listings": [
        {"symbol": r.symbol, "error": r.error,
         "lean": None if r.analysis is None or r.analysis.lean is None
         else {"label": r.analysis.lean.label, "probability": r.analysis.lean.probability}}
        for r in runs
    ]}
