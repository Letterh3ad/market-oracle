"""The fee calculator and the contribution tests, computed on request; the tests read every stored
price of their funds, so on a thread and a connection of their own."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictFloat, StrictInt, StrictStr

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext, worker_context
from oracle.services.contributions_service import FUND, SECOND, ContributionTests, contribution_tests
from oracle.services.fees_service import ASSUMED_GROWTH, fee_drag
from oracle.services.plan_service import plan_summary, save_plan
from oracle.services.settings_service import set_plan

router = APIRouter(prefix="/api/plan")


class Platform(BaseModel):
    platform: StrictStr
    percent_fee: StrictFloat | StrictInt
    percent_cap: StrictFloat | StrictInt
    flat_fee: StrictFloat | StrictInt
    dealing_fee: StrictFloat | StrictInt
    fx_fee: StrictFloat | StrictInt


@router.put("/platform")
async def put_platform(body: Platform, ctx: AppContext = Depends(get_ctx)) -> dict:
    """All six [plan] keys in one write: saved together or not at all."""
    return asdict(set_plan(ctx, body.model_dump()))


class Choice(BaseModel):
    fund: StrictStr | None = None
    monthly: StrictFloat | StrictInt | None = None


@router.put("/choice")
async def put_choice(body: Choice, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The fund held ("default" the numbers' pick) and the monthly amount; a key left out keeps its value."""
    return asdict(save_plan(ctx, body.model_dump(exclude_none=True)))


@router.get("/summary")
async def summary(ctx: AppContext = Depends(get_ctx)) -> dict:
    """"Your plan", on a thread: the kept results come from the research database (its own connection)."""
    return asdict(await asyncio.to_thread(plan_summary, ctx))


@router.get("/fees")
async def fees(monthly: float, years: int = 25, growth: float = ASSUMED_GROWTH * 100, start: float = 0.0,
               ctx: AppContext = Depends(get_ctx)) -> dict:
    """`growth` in % a year."""
    return asdict(fee_drag(ctx, monthly, years, growth / 100, start))


@router.get("/contributions")
async def contributions(monthly: float, lump: float | None = None, years: int = 10, fund: str = FUND,
                        second: str = SECOND,
                        weight: float = 75.0, band: float = 5.0, cash: float = 5.0, cash_rate: float = 0.0,
                        ctx: AppContext = Depends(get_ctx)) -> dict:
    """Percent inputs (`weight`, `band`, `cash`, `cash_rate`) as the CLI takes them; `second` "none"
    skips the mix."""
    other = None if second.strip().lower() in ("", "none") else second.strip().lower()

    def read() -> ContributionTests:
        with worker_context(ctx) as own:
            return contribution_tests(own, monthly, lump, years, growth=fund.strip().lower(), second=other, weight=weight / 100,
                                      band=band / 100, cash_share=cash / 100, cash_rate=cash_rate / 100)

    return contribution_shape(await asyncio.to_thread(read))


def contribution_shape(t: ContributionTests) -> dict:
    out = asdict(t)
    out["starts"] = [[s.start, s.lead] for s in t.starts]
    out["start_span"] = [t.starts[0].start, t.starts[-1].start] if t.starts else None
    for m in out["mixes"]:
        m["result"].pop("path")
    return out
