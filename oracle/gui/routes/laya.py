"""The Laya predictive test's kept results and what scoring the history would take. Scoring and
the test run as jobs (`POST /api/jobs/laya-history`, `/api/jobs/laya-test`)."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext, worker_context
from oracle.services.laya_history_service import last_history_run, plan_history
from oracle.services.laya_test_service import laya_tests, testable_models
from oracle.services.watch_service import DEFAULT_LIST

router = APIRouter(prefix="/api/laya")


@router.get("/tests")
async def tests(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The newest kept result of each run and model, the models a test can read (Laya and the
    stronger reader), and the last scoring run."""
    last = last_history_run(ctx)
    return {"tests": [shapes.laya_test(t) for t in laya_tests(ctx)],
            "models": testable_models(ctx),
            "last_scoring": {**asdict(last), "summary": last.summary} if last else None}


@router.get("/plan")
async def plan(watchlist: str = DEFAULT_LIST, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Pairs a scoring run would score and its time estimate (reads every map: seconds), on a thread."""
    def read():
        with worker_context(ctx) as own:
            return plan_history(own, watchlist)

    return asdict(await asyncio.to_thread(read))
