"""Buttons that start work. Each queues a job and answers at once (202) with it; the page
polls `/api/activity/{id}` for its state and, when done, its result. The Activity tab
lists and cancels them."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from oracle.gui.deps import get_ctx, get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import (
    JobRunner, submit_analyse, submit_backtest, submit_discover, submit_fx, submit_merges, submit_prices, submit_research, submit_tick,
    submit_universe_refresh,
)
from oracle.services.context import AppContext
from oracle.services.watch_service import DEFAULT_LIST

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
async def research(symbol: str, as_of: date | None = None, ctx: AppContext = Depends(get_ctx),
                   runner: JobRunner = Depends(get_runner)) -> dict:
    """One research run (about a minute on the default local model); its report shows on
    the company page when done. `as_of` replays that day, seeing only what existed by then."""
    return shapes.job(submit_research(ctx, runner, symbol, as_of))


@router.post("/tick", status_code=202)
async def tick(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """The whole routine once: prices, FX, fills, analysis, strategy step."""
    return shapes.job(submit_tick(ctx, runner))


@router.post("/universe", status_code=202)
async def universe(runner: JobRunner = Depends(get_runner)) -> dict:
    """Download FinanceDatabase (~17 MB) and replace the universe whole."""
    return shapes.job(submit_universe_refresh(runner))


@router.post("/merges", status_code=202)
async def merges(runner: JobRunner = Depends(get_runner)) -> dict:
    """The companies suffix-blind grouping would join on the stored universe; changes nothing."""
    return shapes.job(submit_merges(runner))


@router.post("/fx", status_code=202)
async def fx(source: list[str] = Query(default=[]), runner: JobRunner = Depends(get_runner)) -> dict:
    """New exchange rates for every currency stored prices use; `source` (repeatable) picks
    which sources to ask, else the configured order."""
    return shapes.job(submit_fx(runner, source))


@router.post("/discover", status_code=202)
async def discover(runner: JobRunner = Depends(get_runner)) -> dict:
    """Companies related to the watched ones, checked and added to the discovered list."""
    return shapes.job(submit_discover(runner))


@router.post("/backtest", status_code=202)
async def backtest(start: date, end: date, watchlist: str = DEFAULT_LIST, slippage_bps: float = 10.0,
                   commission: float = 0.0, ctx: AppContext = Depends(get_ctx),
                   runner: JobRunner = Depends(get_runner)) -> dict:
    """Replay the strategy's rules over the list's stored prices from `start` to `end`, against
    buying and holding; the result is kept (`/api/backtests`). Bad days are refused now."""
    return shapes.job(submit_backtest(ctx, runner, watchlist, start, end, slippage_bps, commission))
