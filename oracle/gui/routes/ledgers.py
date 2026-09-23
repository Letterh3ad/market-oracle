from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.paper_service import (
    ACCOUNTS, account_score, account_view, equity_history, fill_pending, place_buy, place_sell, sells, strategy_log,
    strategy_step,
)

router = APIRouter(prefix="/api/ledgers")


# The page trades the manual account only: the strategy account moves through its rules
# alone, so an `account` field is refused rather than ignored.
class Buy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    amount: float


class Sell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    shares: int | None
    """None sells the whole position."""


@router.post("/manual/buy")
async def buy(body: Buy, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(place_buy(ctx, body.symbol, body.amount))


@router.post("/manual/sell")
async def sell(body: Sell, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(place_sell(ctx, body.symbol, body.shares))


@router.post("/fill")
async def fill(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """Fill every order whose next bar is stored; ones still waiting for it are left out."""
    return [{"symbol": o.order.symbol, "account": o.order.account, "side": o.order.side, "status": o.status,
             "note": o.note} for o in fill_pending(ctx)]


@router.post("/step")
async def step(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """Run the strategy rules over the watched listings' leans and queue what they decide."""
    return [asdict(d) for d in strategy_step(ctx)]


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
