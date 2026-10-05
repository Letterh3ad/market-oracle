"""The Research tab's scoreboard and test registry (research-lab 01), results table (research-lab 06) and weekly
page (research-lab 08)."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictInt, StrictStr

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.lab_registry import Template, commit_test, drop_test, register_test, registration
from oracle.services.lab_service import bar_text, near_check_text, scoreboard
from oracle.services.results_service import results_table, table_json
from oracle.services.strategy_service import STRATEGIES, cost_view, price_trade, strategy_tests, trend_check
from oracle.services.weekly_page_service import CAPABILITIES, default_path, last_page, make_page

router = APIRouter(prefix="/api/lab")


@router.get("/scoreboard")
async def get_scoreboard(ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(scoreboard(ctx))


@router.get("/table")
async def get_table(family: str | None = None, stage: str | None = None, verdict: str | None = None,
                    ctx: AppContext = Depends(get_ctx)) -> dict:
    """`oracle lab table --json`. The scoreboard inside it reads the live database, so it stays on the
    loop thread like /scoreboard."""
    return table_json(results_table(ctx, family, stage, verdict))


@router.get("/page")
async def get_page(ctx: AppContext = Depends(get_ctx)) -> dict:
    last = await asyncio.to_thread(last_page, ctx)
    return {"last": None if last is None else asdict(last), "default": str(default_path(ctx)),
            "capabilities": CAPABILITIES}


class PageBody(BaseModel):
    questions: list[StrictStr] = []


@router.post("/page")
async def post_page(body: PageBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    """`oracle lab page`. On the loop thread: the results table inside it reads the live database."""
    return asdict(make_page(ctx, body.questions))


@router.get("/tests/{key}")
async def get_test(key: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    r = registration(ctx, key)
    return {**asdict(r), "bar_text": bar_text(r), "near_check_text": near_check_text(r)}


class TemplateBody(BaseModel):
    key: StrictStr
    label: StrictStr
    kind: StrictStr
    horizons: list[StrictInt]
    rule: StrictStr
    universe: StrictStr
    expected_sign: StrictStr
    expected_size: StrictStr
    source: StrictStr
    power: StrictStr
    costs: StrictStr
    kill_stage: StrictStr
    parent: StrictStr | None = None
    different_because: StrictStr | None = None
    variants: StrictInt = 1


@router.post("/tests")
async def post_test(body: TemplateBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    data = body.model_dump()
    # On a thread: the near-FAIL check may wait on Ollama's embeddings.
    t = Template(**{**data, "horizons": tuple(data["horizons"])})
    return asdict(await asyncio.to_thread(register_test, ctx, t))


class CommitBody(BaseModel):
    hash: StrictStr


@router.put("/tests/{key}/commit")
async def put_commit(key: str, body: CommitBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(commit_test, ctx, key, body.hash))


@router.delete("/tests/{key}")
async def delete_test(key: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    drop_test(ctx, key)
    return {"dropped": key}


@router.get("/strategies")
async def get_strategies(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Strategy tests with code, each with its newest kept result (or None)."""
    kept = await asyncio.to_thread(strategy_tests, ctx)
    return {"strategies": [{"key": s.name, "label": s.label, "result": asdict(kept[s.name]) if s.name in kept else None}
                           for s in STRATEGIES.values()],
            "kept": [asdict(t) for k, t in kept.items() if k not in STRATEGIES]}


@router.get("/costs")
async def get_costs(value: float | None = None, side: str = "buy", currency: str = "GBP", uk_shares: bool = False,
                    band: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The UK retail cost model from [plan]; with `value`, what one trade of it costs."""
    trade = None if value is None else price_trade(ctx, value, side, currency, uk_shares, band)
    return {"model": asdict(cost_view(ctx)),
            "trade": None if trade is None else {"value": value, "side": side, **asdict(trade), "total": trade.total}}


@router.get("/trend-check")
async def get_trend_check(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The kept trend overlay result reproduced through the strategy engine (never judged again)."""
    return asdict(await asyncio.to_thread(trend_check, ctx))
