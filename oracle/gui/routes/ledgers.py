from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.fx_service import credits
from oracle.services.paper_service import (
    ACCOUNTS, AI_OFF, TRADERS, UNVALIDATED, AccountView, account_score, account_view, ai_step, comparison, decision_log,
    equity_history, fill_pending, pending_orders, place_buy, place_sell, sells, strategy_step,
)
from oracle.services.settings_service import set_ai_account, set_ai_research

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


@router.post("/ai/step")
async def step_ai(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Run the strategy's rules over the research verdicts on the AI account, unless it is off."""
    decisions = ai_step(ctx)
    return {"off": AI_OFF if decisions is None else None, "decisions": [asdict(d) for d in decisions or []],
            "badge": UNVALIDATED}


class AiSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.put("/ai")
async def put_ai(body: AiSwitch, ctx: AppContext = Depends(get_ctx)) -> dict:
    set_ai_account(ctx, body.enabled)
    return {"enabled": ctx.config.ai_account.enabled}


class AiResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_verdict_age_days: StrictInt
    move_threshold: float
    research_budget_min: StrictInt


@router.put("/ai/research")
async def put_ai_research(body: AiResearch, ctx: AppContext = Depends(get_ctx)) -> dict:
    """How ticks keep the AI account's research fresh."""
    set_ai_research(ctx, body.max_verdict_age_days, body.move_threshold, body.research_budget_min)
    return asdict(ctx.config.ai_account)


@router.get("")
async def get_ledgers(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every paper account, strategy first, with the same figures side by side."""
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
        "ai_account": asdict(ctx.config.ai_account),
        "compare": _compare(ctx),
    }


def _compare(ctx: AppContext) -> dict:
    c = comparison(ctx)
    return {**asdict(c), "lines": [{**asdict(line), "return": line.return_} for line in c.lines]}


@router.get("/decisions")
async def get_decisions(account: str = Query(default="strategy", pattern=f"^({'|'.join(TRADERS)})$"),
                        limit: int = Query(default=50, ge=1, le=500), ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """An account's decisions, newest first."""
    return [asdict(d) for d in decision_log(ctx, account, limit)]


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
