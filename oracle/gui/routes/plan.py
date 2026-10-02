"""The boring plan's results, read from the research database. The trend overlay test runs as a
job (`POST /api/jobs/plan-trend`)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.trend_service import trend_tests

router = APIRouter(prefix="/api/plan")


@router.get("/trend")
async def trend(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The newest kept trend overlay test, on a thread (its own research connection)."""
    return {"result": shapes.trend_test(await asyncio.to_thread(trend_tests, ctx))}
