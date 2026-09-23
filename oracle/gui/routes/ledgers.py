from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.fx_service import credits
from oracle.services.paper_service import (
    ACCOUNTS, AccountView, account_score, account_view, equity_history, fill_pending, pending_orders, place_buy,
    place_sell, sells, strategy_log, strategy_step,
)

router = APIRouter(prefix="/api/ledgers")
WAITING = "waiting for the next opening price after it was placed"


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
    """Fill every order whose next bar is stored, and list the ones still waiting for it."""
    outcomes = fill_pending(ctx)
    reported = {o.order.id for o in outcomes}
    return ([shapes.order_outcome(o.order, o.status, o.note) for o in outcomes]
            + [shapes.order_outcome(o, o.status, WAITING) for o in pending_orders(ctx) if o.id not in reported])


@router.post("/step")
async def step(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """Run the strategy rules over the watched listings' leans and queue what they decide."""
    return [asdict(d) for d in strategy_step(ctx)]


@router.get("")
async def get_ledgers(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Both paper accounts, strategy first, with the same figures side by side."""
    views = {name: account_view(ctx, name) for name in ACCOUNTS}
    return {
        "base_currency": ctx.config.base_currency,
        "accounts": [_account(ctx, name, view) for name, view in views.items()],
        "credits": credits(s for v in views.values() for s in v.rate_sources),
        # The strategy's rules as configured, so the page explains the ones actually in force.
        "rules": {
            "min_probability": ctx.config.strategy.min_probability,
            "max_positions": ctx.config.strategy.max_positions,
            "max_position": ctx.config.risk.max_position,
            "cash_floor": ctx.config.risk.cash_floor,
            "stop_loss": ctx.config.strategy.stop_loss,
        },
    }


@router.get("/decisions")
async def get_decisions(limit: int = Query(default=50, ge=1, le=500), ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """The strategy account's decisions, newest first."""
    return [asdict(d) for d in strategy_log(ctx, limit)]


def _account(ctx: AppContext, name: str, view: AccountView) -> dict:
    score = account_score(ctx, name)
    return {
        "name": name, "equity": score.equity, "start": score.start, "return": score.return_,
        "cash": view.cash, "realised": score.realised, "unrealised": score.unrealised,
        "costs": score.costs, "max_fall": score.max_fall,
        "positions": [asdict(p) for p in view.positions],
        "pending": [asdict(o) for o in view.pending],
        "sells": [{**asdict(s), "realised": s.realised} for s in reversed(sells(ctx, name))],
        "history": equity_history(ctx, name),
    }
