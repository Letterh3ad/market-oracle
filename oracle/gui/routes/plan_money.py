"""The fee calculator and the contribution tests, computed on request; the tests read every stored
price of their funds, so on a thread and a connection of their own."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext, worker_context
from oracle.services.contributions_service import SECOND, ContributionTests, contribution_tests
from oracle.services.fees_service import ASSUMED_GROWTH, fee_drag

router = APIRouter(prefix="/api/plan")


@router.get("/fees")
async def fees(monthly: float, years: int = 25, growth: float = ASSUMED_GROWTH * 100, start: float = 0.0,
               ctx: AppContext = Depends(get_ctx)) -> dict:
    """`growth` in % a year."""
    return asdict(fee_drag(ctx, monthly, years, growth / 100, start))


@router.get("/contributions")
async def contributions(monthly: float, lump: float | None = None, years: int = 10, second: str = SECOND,
                        weight: float = 75.0, band: float = 5.0, cash: float = 5.0, cash_rate: float = 0.0,
                        ctx: AppContext = Depends(get_ctx)) -> dict:
    """Percent inputs (`weight`, `band`, `cash`, `cash_rate`) as the CLI takes them; `second` "none"
    skips the mix."""
    other = None if second.strip().lower() in ("", "none") else second.strip().upper()

    def read() -> ContributionTests:
        with worker_context(ctx) as own:
            return contribution_tests(own, monthly, lump, years, second=other, weight=weight / 100,
                                      band=band / 100, cash_share=cash / 100, cash_rate=cash_rate / 100)

    return contribution_shape(await asyncio.to_thread(read))


def contribution_shape(t: ContributionTests) -> dict:
    out = asdict(t)
    out["starts"] = [[s.start, s.lead] for s in t.starts]
    for m in out["mixes"]:
        m["result"].pop("path")
    return out
