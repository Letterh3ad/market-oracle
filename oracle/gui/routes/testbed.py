"""The signal testbed's coverage, read from the research database. The build itself is a job
(`POST /api/jobs/testbed`)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.testbed_service import testbed_coverage

router = APIRouter(prefix="/api/testbed")


@router.get("/coverage")
async def coverage(ctx: AppContext = Depends(get_ctx)) -> dict:
    """On a thread: the coverage join reads every member-day (seconds on the full testbed). It
    opens the research database on that thread, so the live connection is never shared."""
    return shapes.testbed_coverage(await asyncio.to_thread(testbed_coverage, ctx))
