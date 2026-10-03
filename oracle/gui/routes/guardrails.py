"""The Plan tab's guardrails: the drawdown preview, the no-sell rule and the pending plan change."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictStr

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext, worker_context
from oracle.services.guardrails_service import (
    PlanChangeResult, cancel_pending, change_plan, drawdown_preview, guardrails, settle,
)

router = APIRouter(prefix="/api/plan")


def change_shape(r: PlanChangeResult) -> dict:
    """The plan in effect, as the plan setters always answered, plus what applied now and what waits."""
    return {**asdict(r.plan), "applied": list(r.applied), "pending": asdict(r.pending) if r.pending else None}


@router.get("/guardrails")
async def get_guardrails(ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(guardrails(ctx))


class Rule(BaseModel):
    rule: StrictStr


@router.put("/rule")
async def put_rule(body: Rule, ctx: AppContext = Depends(get_ctx)) -> dict:
    """A first rule applies at once; changing or clearing one waits 7 days."""
    return change_shape(change_plan(ctx, {"rule": body.rule}))


@router.delete("/pending")
async def delete_pending(ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(cancel_pending(ctx))


@router.get("/drawdowns")
async def drawdowns(pot: float | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """`pot` in GBP, used for this answer only (never stored); left out, an example pot. Settled here on
    the app's context too, since the worker's copy is thrown away."""
    settle(ctx)

    def read():
        with worker_context(ctx) as own:
            return drawdown_preview(own, pot)

    return asdict(await asyncio.to_thread(read))
