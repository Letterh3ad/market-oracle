"""Buttons that start work. Each queues a job and answers at once (202) with it; the page
polls `/api/activity/{id}` for its state and, when done, its result. The Activity tab
lists and cancels them."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from oracle.gui.deps import get_ctx, get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import (
    JobRunner, submit_analyse, submit_ask, submit_backfill, submit_backtest, submit_cutoff_probe, submit_discover, submit_fx,
    submit_edges, submit_fill, submit_history, submit_industry, submit_laya, submit_laya_history, submit_laya_test, submit_merges, submit_midday, submit_prices, submit_reader, submit_rerun, submit_research, submit_signal_test, submit_testbed, submit_tick, submit_xbrl, submit_universe_refresh,
)
from oracle.services.context import AppContext
from oracle.services.laya_service import MODEL_TAG
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
async def research(symbol: str, as_of: date | None = None, model: str | None = None,
                   ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """One research run (about a minute on the default local model); its report shows on
    the company page when done. `as_of` replays that day, seeing only what existed by then;
    `model` runs every stage on that profile instead of the slots."""
    return shapes.job(submit_research(ctx, runner, symbol, as_of, model or None))


@router.post("/laya", status_code=202)
async def laya(symbol: str, ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Score the company's stored articles Laya has not scored yet; it waits while model work runs."""
    return shapes.job(submit_laya(ctx, runner, symbol))


@router.post("/edges", status_code=202)
async def edges(symbol: str | None = None, watchlist: str | None = None, ctx: AppContext = Depends(get_ctx),
                runner: JobRunner = Depends(get_runner)) -> dict:
    """Read the company's (or the list's) headlines and US 10-Ks not yet read for supplier, customer,
    competitor and partner ties; it waits while model work runs."""
    return shapes.job(submit_edges(ctx, runner, symbol, watchlist))


@router.post("/industry", status_code=202)
async def industry(symbol: str | None = None, ctx: AppContext = Depends(get_ctx),
                   runner: JobRunner = Depends(get_runner)) -> dict:
    """Research industry: new headlines of the company (or the tick's daily set) and its industry map,
    scored with Laya per related pair into daily features; it waits while model work runs."""
    return shapes.job(submit_industry(ctx, runner, symbol))


@router.post("/tick", status_code=202)
async def tick(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """The whole routine once: prices, FX, fills, analysis, strategy step."""
    return shapes.job(submit_tick(ctx, runner))


@router.post("/midday", status_code=202)
async def midday(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Discovery, then research on stale listings; no prices, steps or orders."""
    return shapes.job(submit_midday(ctx, runner))


@router.post("/fill", status_code=202)
async def fill(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Prices, FX and fills only, so orders waiting on an open fill now."""
    return shapes.job(submit_fill(ctx, runner))


@router.post("/universe", status_code=202)
async def universe(runner: JobRunner = Depends(get_runner)) -> dict:
    """Download FinanceDatabase (~17 MB) and replace the universe whole."""
    return shapes.job(submit_universe_refresh(runner))


@router.post("/backfill", status_code=202)
async def backfill(runner: JobRunner = Depends(get_runner)) -> dict:
    """Add the index members the universe lacks from Yahoo, a bounded batch a run."""
    return shapes.job(submit_backfill(runner))


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


@router.post("/ask", status_code=202)
async def ask(question: str, refines: int | None = None, model: str | None = None,
              ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Save a question (or, with `refines`, a follow-up narrowing that one) and answer it with a
    shortlist, on the discovery slot or `model`; a blank or overlong question is refused now."""
    return shapes.job(submit_ask(ctx, runner, question, refines, model or None))


@router.post("/ask/{question_id}/rerun", status_code=202)
async def rerun(question_id: int, model: str | None = None, ctx: AppContext = Depends(get_ctx),
                runner: JobRunner = Depends(get_runner)) -> dict:
    """Answer a saved question again; the new answer marks what is new since the last."""
    return shapes.job(submit_rerun(ctx, runner, question_id, model or None))


@router.post("/backtest", status_code=202)
async def backtest(start: date, end: date, watchlist: str = DEFAULT_LIST, slippage_bps: float = 10.0,
                   commission: float = 0.0, walk_forward: bool = False, regime_ai: bool = False,
                   ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Replay the strategy's rules over the list's stored prices from `start` to `end`, against
    buying and holding; the result is kept (`/api/backtests`). Bad days are refused now. With
    `walk_forward`, also each line over six-month segments and the regime_wf line. `regime_ai`
    adds the replayed model tilt line: a model call per day, with progress."""
    return shapes.job(submit_backtest(ctx, runner, watchlist, start, end, slippage_bps, commission, walk_forward,
                                      regime_ai))


@router.post("/cutoff-probe", status_code=202)
async def cutoff_probe(model: str | None = None, ctx: AppContext = Depends(get_ctx),
                       runner: JobRunner = Depends(get_runner)) -> dict:
    """Ask the research slot's model (or `model`) dated questions and keep its measured cutoff
    (`/api/replay/cutoff`); an unknown profile is refused now."""
    return shapes.job(submit_cutoff_probe(ctx, runner, model or None))


@router.post("/laya-history", status_code=202)
async def laya_history(watchlist: str = DEFAULT_LIST, allow_cpu: bool = False, ctx: AppContext = Depends(get_ctx),
                       runner: JobRunner = Depends(get_runner)) -> dict:
    """Score the backfilled history of a list and its maps with Laya per (article, target) pair
    (hours); it waits while live model work runs and for GPU room unless `allow_cpu`, and a retry
    resumes."""
    return shapes.job(submit_laya_history(ctx, runner, watchlist, allow_cpu))


@router.post("/laya-test", status_code=202)
async def laya_test(watchlist: str = DEFAULT_LIST, sensitivity: bool = False, own_only: bool = False,
                    model: str = MODEL_TAG, ctx: AppContext = Depends(get_ctx),
                    runner: JobRunner = Depends(get_runner)) -> dict:
    """The pre-registered predictive test (or its sensitivity or own-news-only run) over `model`'s scored
    history (Laya's by default)."""
    return shapes.job(submit_laya_test(ctx, runner, sensitivity, watchlist, model, own_only))


@router.post("/history", status_code=202)
async def history(watchlist: str = DEFAULT_LIST, ctx: AppContext = Depends(get_ctx),
                  runner: JobRunner = Depends(get_runner)) -> dict:
    """The history backfill of a list and its industry maps (hours); it waits while live work runs
    and resumes where it stopped."""
    return shapes.job(submit_history(ctx, runner, watchlist))


@router.post("/testbed", status_code=202)
async def testbed(refresh: bool = False, ctx: AppContext = Depends(get_ctx),
                  runner: JobRunner = Depends(get_runner)) -> dict:
    """Build the signal testbed in the research database (hours the first time), or with `refresh`
    re-read its membership and top up current members; it waits while live work runs and resumes
    where it stopped."""
    return shapes.job(submit_testbed(ctx, runner, refresh))


@router.post("/xbrl", status_code=202)
async def xbrl(refresh: bool = False, ctx: AppContext = Depends(get_ctx),
               runner: JobRunner = Depends(get_runner)) -> dict:
    """Fetch the testbed members' SEC fundamentals into the research database (about an hour the
    first time; `refresh` fetches every member again); it waits while live work runs and resumes."""
    return shapes.job(submit_xbrl(ctx, runner, refresh))


@router.post("/signal-test", status_code=202)
async def signal_test(name: str, train_only: bool = False, ctx: AppContext = Depends(get_ctx),
                      runner: JobRunner = Depends(get_runner)) -> dict:
    """The pre-registered test of one signal on the testbed (minutes; `train_only`: 2010-2019 alone),
    kept in the research database; it waits while live work or a testbed build runs."""
    return shapes.job(submit_signal_test(ctx, runner, name, train_only))


@router.post("/reader", status_code=202)
async def reader(watchlist: str = DEFAULT_LIST, own_only: bool = False, ctx: AppContext = Depends(get_ctx),
                 runner: JobRunner = Depends(get_runner)) -> dict:
    """The stronger reader over a list's history backfill (hours); it waits while model work runs or a
    scheduled tick is near, and resumes where it stopped."""
    return shapes.job(submit_reader(ctx, runner, watchlist, own_only))
