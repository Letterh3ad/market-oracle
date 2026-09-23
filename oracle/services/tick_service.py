"""The daily routine as one run: prices, FX, fills, analysis, AI research, strategy step, AI
step. Each stage runs even when an earlier one failed, since each works from whatever is
stored."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime

from oracle.broker.rules import Decision
from oracle.services.analysis_service import analyse_watched
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.fx_service import update_fx
from oracle.services.jobs_service import single_flight
from oracle.services.paper_service import ai_step, fill_pending, strategy_step
from oracle.services.prices_service import update_watched
from oracle.services.research_service import refresh_research
from oracle.store.ticks import INTERRUPTED, Stage, TickRepo, TickRun
from oracle.store.watchlists import WatchlistRepo

__all__ = ["STAGES", "Stage", "TickRun", "last_tick", "record_skipped", "run_tick", "ticked_since"]

log = logging.getLogger(__name__)

JOB = "tick"
LEASE_S = 3600.0

Say = Callable[[str], None]
StageRun = Callable[[AppContext, Say], Awaitable[tuple[str, str | None]]]
"""Returns a summary and the first error among its listings or currencies; may `say` where
it is, for a long stage's progress."""


async def _prices(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    run = await update_watched(ctx, fx=False)
    if not run.listings:
        return "nothing watched", None
    added = sum(o.added for o in run.listings)
    skipped = sum(1 for o in run.listings if o.skipped)
    tried = len(run.listings) - skipped
    first = run.failed[0] if run.failed else None
    summary = f"{tried - len(run.failed)} of {tried} listings updated, {added} new bars"
    return summary + (f", {skipped} skipped (no data lately)" if skipped else ""), first and first.error


async def _fx(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    done = await update_fx(ctx)
    failed = [f for f in done if f.error]
    return (f"{len(done) - len(failed)} of {len(done)} currencies updated, {sum(f.added for f in done)} new rates",
            failed[0].error if failed else None)


async def _fill(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    counts = Counter(o.status.value for o in fill_pending(ctx))
    return ", ".join(f"{counts[s]} {s}" for s in ("filled", "rejected")), None


async def _analyse(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    runs = await analyse_watched(ctx)
    failed = [r for r in runs if r.error]
    return (f"{len(runs) - len(failed)} of {len(runs)} listings analysed",
            f"{failed[0].symbol}: {failed[0].error}" if failed else None)


async def _step(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    return _decided(ctx, strategy_step(ctx)), None


async def _research(ctx: AppContext, say: Say) -> tuple[str, str | None]:
    if not ctx.config.ai_account.enabled:
        return f"{OFF}: nothing researched", None
    done = await refresh_research(ctx, say)
    if not done.due:
        return "nothing due", None
    parts = [f"{len(done.researched)} of {done.due} due researched"]
    if done.failed:
        parts.append(f"{len(done.failed)} failed")
    if done.deferred:
        parts.append(f"{len(done.deferred)} deferred ({done.deferred_why})")
    return ", ".join(parts), ": ".join(done.failed[0]) if done.failed else None


async def _ai_step(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    decisions = ai_step(ctx)
    return (f"{OFF}: nothing traded", None) if decisions is None else (_decided(ctx, decisions), None)


def _decided(ctx: AppContext, decisions: list[Decision]) -> str:
    counts = Counter(d.action.value for d in decisions)
    # The rules say nothing about a listing they leave as it is.
    unchanged = len(set(WatchlistRepo(ctx.conn).all_symbols()) - {d.symbol for d in decisions})
    return ", ".join(f"{counts[a]} {a}" for a in ("buy", "sell", "skip")) + f", {unchanged} no change"


STAGES: dict[str, StageRun] = {"prices": _prices, "fx": _fx, "fill": _fill, "analyse": _analyse,
                               "research": _research, "step": _step, "ai_step": _ai_step}
OFF = "AI account off"


async def run_tick(ctx: AppContext, trigger: str = "manual",
                   on_stage: Callable[[str], None] | None = None) -> TickRun:
    """Run every stage once, in one process at a time, and store the outcome. `on_stage` is
    told each stage's name as it starts, then any progress the stage reports."""
    started = ctx.clock.utcnow()
    stages: list[Stage] = []
    with single_flight(ctx, JOB, LEASE_S) as held:

        def say(text: str) -> None:
            # Progress doubles as the heartbeat, so a long research stage keeps the lock.
            held.check()
            if on_stage:
                on_stage(text)

        try:
            for name, stage in STAGES.items():
                say(name)
                stages.append(await _run_stage(ctx, name, stage, say))
        except BaseException as exc:
            # Stopped part way (server shutdown, lock taken over): still leave a record.
            _save(ctx, TickRun(trigger, started, ctx.clock.utcnow(), tuple(stages),
                               f"{INTERRUPTED}: {exc or type(exc).__name__}"))
            raise
    return _save(ctx, TickRun(trigger, started, ctx.clock.utcnow(), tuple(stages)))


def _save(ctx: AppContext, run: TickRun) -> TickRun:
    return replace(run, id=TickRepo(ctx.conn).save(run))


async def _run_stage(ctx: AppContext, name: str, stage: StageRun, say: Say) -> Stage:
    try:
        summary, error = await stage(ctx, say)
    except Exception as exc:
        if not isinstance(exc, ServiceError):
            log.exception("tick stage %s failed", name)
        return Stage(name, "failed", "did not run", str(exc) or type(exc).__name__)
    return Stage(name, "partial" if error else "ok", summary, error)


def record_skipped(ctx: AppContext, trigger: str, note: str) -> TickRun:
    """A tick that never started, stored so the user can see why."""
    now = ctx.clock.utcnow()
    return _save(ctx, TickRun(trigger, now, now, (), note))


def ticked_since(ctx: AppContext, at: datetime) -> bool:
    """Whether any tick (skipped ones too) finished at or after `at`."""
    return TickRepo(ctx.conn).finished_since(at)


def last_tick(ctx: AppContext) -> TickRun | None:
    found = TickRepo(ctx.conn).recent(1)
    return found[0] if found else None
