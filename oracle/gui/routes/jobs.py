"""Buttons that start work. Each queues a job and answers at once (202) with it; the page
polls `/api/activity/{id}` for its state and, when done, its result. The Activity tab
lists and cancels them."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx, get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import (
    JobRunner, submit_analyse, submit_prices, submit_research, submit_tick, submit_universe_refresh,
)
from oracle.services.context import AppContext

router = APIRouter(prefix="/api/jobs")


@router.post("/prices", status_code=202)
async def prices(symbol: str | None = None, ctx: AppContext = Depends(get_ctx),
                 runner: JobRunner = Depends(get_runner)) -> dict:
    """Every watched listing, or just `symbol` (watched or not)."""
    return shapes.job(submit_prices(ctx, runner, symbol))


@router.post("/analyse", status_code=202)
async def analyse(symbol: str | None = None, ctx: AppContext = Depends(get_ctx),
                  runner: JobRunner = Depends(get_runner)) -> dict:
    """Every watched listing, or just `symbol` (watched or not)."""
    return shapes.job(submit_analyse(ctx, runner, symbol))


@router.post("/research", status_code=202)
async def research(symbol: str, ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """One research run (about a minute on the default local model); its report shows on
    the company page when done."""
    return shapes.job(submit_research(ctx, runner, symbol))


@router.post("/tick", status_code=202)
async def tick(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """The whole routine once: prices, FX, fills, analysis, strategy step."""
    return shapes.job(submit_tick(ctx, runner))


@router.post("/universe", status_code=202)
async def universe(runner: JobRunner = Depends(get_runner)) -> dict:
    """Download FinanceDatabase (~17 MB) and replace the universe whole."""
    return shapes.job(submit_universe_refresh(runner))
