"""The daily routine as one run: prices, FX, fills, analysis, strategy step. Each stage runs
even when an earlier one failed, since each works from whatever is stored."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import replace

from oracle.services.analysis_service import analyse_watched
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.fx_service import update_fx
from oracle.services.jobs_service import single_flight
from oracle.services.paper_service import fill_pending, strategy_step
from oracle.services.prices_service import update_watched
from oracle.store.ticks import Stage, TickRepo, TickRun

__all__ = ["STAGES", "Stage", "TickRun", "last_tick", "run_tick"]

log = logging.getLogger(__name__)

JOB = "tick"
LEASE_S = 3600.0

StageRun = Callable[[AppContext], Awaitable[tuple[str, str | None]]]
"""Returns a summary and the first error among its listings or currencies."""


async def _prices(ctx: AppContext) -> tuple[str, str | None]:
    run = await update_watched(ctx, fx=False)
    if not run.listings:
        return "nothing watched", None
    added = sum(o.added for o in run.listings)
    first = run.failed[0] if run.failed else None
    return (f"{len(run.listings) - len(run.failed)} of {len(run.listings)} listings updated, {added} new bars",
            first and first.error)


async def _fx(ctx: AppContext) -> tuple[str, str | None]:
    done = await update_fx(ctx)
    failed = [f for f in done if f.error]
    return (f"{len(done) - len(failed)} of {len(done)} currencies updated, {sum(f.added for f in done)} new rates",
            failed[0].error if failed else None)


async def _fill(ctx: AppContext) -> tuple[str, str | None]:
    counts = Counter(o.status.value for o in fill_pending(ctx))
    return ", ".join(f"{counts[s]} {s}" for s in ("filled", "rejected")), None


async def _analyse(ctx: AppContext) -> tuple[str, str | None]:
    runs = await analyse_watched(ctx)
    failed = [r for r in runs if r.error]
    return (f"{len(runs) - len(failed)} of {len(runs)} listings analysed",
            f"{failed[0].symbol}: {failed[0].error}" if failed else None)


async def _step(ctx: AppContext) -> tuple[str, str | None]:
    counts = Counter(d.action.value for d in strategy_step(ctx))
    return ", ".join(f"{counts[a]} {a}" for a in ("buy", "sell", "skip")), None


STAGES: dict[str, StageRun] = {"prices": _prices, "fx": _fx, "fill": _fill, "analyse": _analyse, "step": _step}


async def run_tick(ctx: AppContext, trigger: str = "manual") -> TickRun:
    """Run every stage once, in one process at a time, and store the outcome."""
    started = ctx.clock.utcnow()
    with single_flight(ctx, JOB, LEASE_S) as held:
        stages = []
        for name, stage in STAGES.items():
            held.check()
            stages.append(await _run_stage(ctx, name, stage))
    run = TickRun(trigger, started, ctx.clock.utcnow(), tuple(stages))
    return replace(run, id=TickRepo(ctx.conn).save(run))


async def _run_stage(ctx: AppContext, name: str, stage: StageRun) -> Stage:
    try:
        summary, error = await stage(ctx)
    except Exception as exc:
        if not isinstance(exc, ServiceError):
            log.exception("tick stage %s failed", name)
        return Stage(name, "failed", "did not run", str(exc) or type(exc).__name__)
    return Stage(name, "partial" if error else "ok", summary, error)


def last_tick(ctx: AppContext) -> TickRun | None:
    found = TickRepo(ctx.conn).recent(1)
    return found[0] if found else None
