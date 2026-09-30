"""The stronger reader's plan and last run. The run itself is a job (`POST /api/jobs/reader`)."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext, worker_context
from oracle.services.reader_service import ReaderPlan, last_reader_run, plan_reader
from oracle.services.watch_service import DEFAULT_LIST

router = APIRouter(prefix="/api/reader")


@router.get("")
async def reader_status(ctx: AppContext = Depends(get_ctx)) -> dict:
    last = last_reader_run(ctx)
    return {"model": ctx.config.laya.reader_model,
            "last_run": {**asdict(last), "summary": last.summary} if last else None}


@router.get("/plan")
async def reader_plan(watchlist: str = DEFAULT_LIST, own_only: bool = False, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Headlines a run would read and how long it would take; on a thread and connection of its own
    (it walks the list's backfill)."""
    def read() -> ReaderPlan:
        with worker_context(ctx) as own:
            return plan_reader(own, watchlist, own_only=own_only)

    return asdict(await asyncio.to_thread(read))
