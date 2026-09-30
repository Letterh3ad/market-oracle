"""What the history backfill has stored, per company and quarter. The backfill itself is a job
(`POST /api/jobs/history`)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext, worker_context
from oracle.services.history_service import Coverage, coverage
from oracle.services.watch_service import DEFAULT_LIST

router = APIRouter(prefix="/api/history")


@router.get("/coverage")
async def history_coverage(watchlist: str = DEFAULT_LIST, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Reads every list company's industry map (15 s on the live list), so on a thread and a
    connection of its own."""
    def read() -> Coverage:
        with worker_context(ctx) as own:
            return coverage(own, watchlist)

    return shapes.coverage(await asyncio.to_thread(read))
