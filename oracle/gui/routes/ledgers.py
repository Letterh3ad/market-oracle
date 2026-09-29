from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from oracle.gui.deps import get_ctx, get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import JobRunner, queue_stale_research, stale_research_jobs, submit_line_step
from oracle.services.context import AppContext
from oracle.services.fx_service import credits
from oracle.services.paper_service import (
    ACCOUNTS, AI_OFF, REGIME, REGIME_AI, REGIME_V2, TRADERS, UNVALIDATED, AccountView, account_score, account_view, ai_step, comparison, decision_log,
    equity_history, fill_pending, pending_orders, place_buy, place_sell, sells, strategy_step,
)
from oracle.services.models_service import check_model_profile
from oracle.services.regime_service import regime_card
from oracle.services.settings_service import (
    set_ai_account, set_ai_research, set_regime_account, set_regime_ai_account, set_regime_v2_account,
)
from oracle.services.tick_service import discovery_line

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


@router.post("/regime/step", status_code=202)
async def step_regime(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Queue the regime account's step on the tick queue; the finished job carries its decisions."""
    return shapes.job(submit_line_step(ctx, runner, REGIME))


@router.post("/regime-ai/step", status_code=202)
async def step_regime_ai(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Queue the regime-ai account's step (asking the model for the day's tilt once) on the tick queue."""
    return shapes.job(submit_line_step(ctx, runner, REGIME_AI))


@router.post("/regime-v2/step", status_code=202)
async def step_regime_v2(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Queue the regime-v2 account's step on the tick queue."""
    return shapes.job(submit_line_step(ctx, runner, REGIME_V2))


@router.put("/regime-v2")
async def put_regime_v2(body: AiSwitch, ctx: AppContext = Depends(get_ctx)) -> dict:
    set_regime_v2_account(ctx, body.enabled)
    return {"enabled": ctx.config.regime_v2_account.enabled}


@router.get("/regime-v2")
async def get_regime_v2(limit: int = Query(default=20, ge=1, le=200), ctx: AppContext = Depends(get_ctx)) -> dict:
    """The regime card with regime-v2's rotation settings; rotation sales say whom they made room for."""
    return _card(ctx, limit, REGIME_V2)


@router.put("/regime")
async def put_regime(body: AiSwitch, ctx: AppContext = Depends(get_ctx)) -> dict:
    set_regime_account(ctx, body.enabled)
    return {"enabled": ctx.config.regime_account.enabled}


@router.get("/regime")
async def get_regime(limit: int = Query(default=20, ge=1, le=200), ctx: AppContext = Depends(get_ctx)) -> dict:
    """The regime card: the last step's mood, flags, preset and final weights, warnings, good signs
    and health, and the account's recent decisions with what each lean was made of."""
    return _card(ctx, limit, REGIME)


class RegimeAiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    model: StrictStr | None = None
    """A model profile's name; "" the research slot's."""


@router.put("/regime-ai")
async def put_regime_ai(body: RegimeAiSettings, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The regime-ai account's switch and the model that tilts it; a field left out stays as it is."""
    check_model_profile(ctx, body.model or None)
    set_regime_ai_account(ctx, body.enabled, body.model)
    return asdict(ctx.config.regime_ai_account)


@router.get("/regime-ai")
async def get_regime_ai(limit: int = Query(default=20, ge=1, le=200), ctx: AppContext = Depends(get_ctx)) -> dict:
    """The regime card with the day's tilt beside the rules weights, its reason or failure, and the model."""
    return _card(ctx, limit, REGIME_AI)


def _card(ctx: AppContext, limit: int, account: str) -> dict:
    card = regime_card(ctx, limit, account)
    return {**asdict(card), "decisions": [asdict(d) for d in card.decisions]}


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


@router.get("/ai/stale")
async def get_stale(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """The watched listings a tick would research now, and those already queued."""
    stale = stale_research_jobs(ctx, runner)
    return {"due": stale.due, "waiting": stale.waiting}


@router.post("/ai/research-stale", status_code=202)
async def research_stale(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Queue research on every due listing not already queued; they run one after another."""
    done = queue_stale_research(ctx, runner)
    return {"queued": [shapes.job(j) for j in done.jobs], "waiting": done.waiting,
            "refused": [list(r) for r in done.refused]}


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
        "regime_account": asdict(ctx.config.regime_account),
        "regime_ai_account": asdict(ctx.config.regime_ai_account),
        "regime_v2_account": asdict(ctx.config.regime_v2_account),
        "discovery": discovery_line(ctx),
        "compare": _compare(ctx),
    }


def _compare(ctx: AppContext) -> dict:
    c = comparison(ctx)
    return {**asdict(c), "lines": [shapes.line(line) for line in c.lines]}


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
