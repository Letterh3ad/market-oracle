from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.paper_service import (
    ACCOUNTS, account_score, account_view, equity_history, sells, strategy_log,
)

router = APIRouter(prefix="/api/ledgers")


@router.get("")
async def get_ledgers(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Both paper accounts, strategy first, with the same figures side by side."""
    return {"base_currency": ctx.config.base_currency, "accounts": [_account(ctx, name) for name in ACCOUNTS]}


@router.get("/decisions")
async def get_decisions(limit: int = Query(default=50, ge=1, le=500), ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """The strategy account's decisions, newest first."""
    return [asdict(d) for d in strategy_log(ctx, limit)]


def _account(ctx: AppContext, name: str) -> dict:
    score, view = account_score(ctx, name), account_view(ctx, name)
    return {
        "name": name, "equity": score.equity, "start": score.start, "return": score.return_,
        "cash": view.cash, "realised": score.realised, "unrealised": score.unrealised,
        "costs": score.costs, "max_fall": score.max_fall,
        "positions": [asdict(p) for p in view.positions],
        "pending": [asdict(o) for o in view.pending],
        "sells": [{**asdict(s), "realised": s.realised} for s in reversed(sells(ctx, name))],
        "history": equity_history(ctx, name),
    }
