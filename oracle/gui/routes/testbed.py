"""The signal testbed's coverage and the signal tests' kept results, read from the research
database. The build and each test are jobs (`POST /api/jobs/testbed`, `POST /api/jobs/signal-test`)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.event_study_service import event_studies
from oracle.services.insider_service import insider_coverage
from oracle.services.signal_test_service import SIGNALS, signal_tests
from oracle.services.testbed_service import testbed_coverage
from oracle.services.xbrl_service import fundamentals_coverage

router = APIRouter(prefix="/api")


@router.get("/testbed/coverage")
async def coverage(ctx: AppContext = Depends(get_ctx)) -> dict:
    """On a thread: the coverage join reads every member-day (seconds on the full testbed). It
    opens the research database on that thread, so the live connection is never shared."""
    return shapes.testbed_coverage(await asyncio.to_thread(testbed_coverage, ctx))


@router.get("/signals")
async def signals(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The pre-registered signals and each one's newest result, on a thread (its own research connection)."""
    full = await asyncio.to_thread(signal_tests, ctx)
    return shapes.signal_list(SIGNALS.values(), full, await asyncio.to_thread(signal_tests, ctx, True))


@router.get("/xbrl/coverage")
async def xbrl_coverage(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The SEC fundamentals' coverage per year, on a thread (it derives every member's filings)."""
    return shapes.xbrl_coverage(await asyncio.to_thread(fundamentals_coverage, ctx))


@router.get("/insiders/coverage")
async def insiders_coverage(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Form 4 trades and typed filings stored per year, on a thread."""
    return shapes.insider_coverage(await asyncio.to_thread(insider_coverage, ctx))


@router.get("/events")
async def events(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The newest kept typed-event study, full and train-only, on a thread."""
    kept = await asyncio.to_thread(event_studies, ctx)
    return {k: shapes.event_study(kept.get(k)) for k in ("full", "train")}
