"""The daily routine as one run: prices, FX, fills, analysis, discovery, AI research, strategy
step, AI step. Each stage runs even when an earlier one failed, since each works from
whatever is stored. The midday pass reuses the discovery and research stages, with the auto
questions answered again between them."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

from oracle.broker.rules import Decision
from oracle.services.analysis_service import analyse_watched
from oracle.services.context import AppContext
from oracle.services.discovery_chat_service import TYPICAL_ANSWER_MIN, answer_auto_questions
from oracle.services.discovery_service import DISCOVERED_LIST, RELATION_AUTO, DiscoveryRun, discover, discovered
from oracle.services.errors import JobAlreadyRunning, ServiceError
from oracle.services.fx_service import update_fx
from oracle.services.jobs_service import single_flight
from oracle.services.paper_service import ai_step, fill_pending, strategy_step, traded_by
from oracle.services.prices_service import update_watched
from oracle.services.replay_score import update_study_prices
from oracle.services.research_service import UNVALIDATED_TAG, pc_in_use, refresh_research
from oracle.store.replay import ReplayRepo
from oracle.store.ticks import INTERRUPTED, MIDDAY, TICK, Stage, TickRepo, TickRun
from oracle.store.watchlists import WatchlistRepo

__all__ = [
    "KIND_LABELS", "MIDDAY", "MIDDAY_STAGES", "STAGES", "Stage", "TICK", "TickRun", "discover_cap_min",
    "discovery_line", "last_midday", "last_tick", "record_skipped", "run_midday", "run_tick", "ticked_since",
]

log = logging.getLogger(__name__)

JOB = "tick"
LEASE_S = 3600.0
DISCOVER_CAP_MIN = 10
# Auto questions one midday pass answers; each is a whole answer (plan, search, read, name, validate).
MAX_AUTO_QUESTIONS = 3
QUESTIONS_CAP_MIN = 15

Say = Callable[[str], None]
StageRun = Callable[[AppContext, Say], Awaitable[tuple[str, str | None]]]
"""Returns a summary and the first error among its listings or currencies; may `say` where
it is, for a long stage's progress."""


async def _prices(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    run = await update_watched(ctx, fx=False)
    study = await _study_prices(ctx)
    if not run.listings:
        return "nothing watched" + study, None
    added = sum(o.added for o in run.listings)
    skipped = sum(1 for o in run.listings if o.skipped)
    tried = len(run.listings) - skipped
    first = run.failed[0] if run.failed else None
    summary = f"{tried - len(run.failed)} of {tried} listings updated, {added} new bars"
    return summary + (f", {skipped} skipped (no data lately)" if skipped else "") + study, first and first.error


async def _study_prices(ctx: AppContext) -> str:
    """Keeps the replay study's interim score current while nobody opens it. Its listings are
    rarely watched, and a failure here is the study's, never the tick's."""
    if ReplayRepo(ctx.conn).current() is None:
        return ""
    try:
        out = await update_study_prices(ctx)
    except Exception as exc:  # the tick's watched prices must still count, whatever broke here
        log.exception("replay study prices failed")
        return f"; replay study prices failed: {exc}"
    failed = sum(1 for o in out if o.error)
    return (f"; replay study: {len(out) - failed} of {len(out)} listings refetched"
            + (f", {failed} failed" if failed else "")) if out else "; replay study prices current"


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
    return _decided(ctx, "strategy", strategy_step(ctx)), None


def discover_cap_min(ctx: AppContext, budget_min: float | None = None) -> float:
    """How long the discover stage may run: it delays research, so at most 10 minutes and
    never more than a third of the research budget (the tick's unless another is given)."""
    return min(DISCOVER_CAP_MIN, (budget_min or ctx.config.ai_account.research_budget_min) / 3)


def _held_back(ctx: AppContext, what: str, run: str) -> str | None:
    """Why discovery of any kind does not run now, if it does not."""
    if not ctx.config.discovery.enabled:
        return f"discovery is off in Settings: nothing {what}"
    if not ctx.config.automation.enabled:
        return f"automation is off: nothing {what}"
    if pc_in_use(ctx):
        return f"PC in use: discovery waits for the next {run}"
    return None


async def _discover(ctx: AppContext, _: Say, cap_min: float | None = None, run: str = "tick") -> tuple[str, str | None]:
    """`run` names what it is part of, for its messages."""
    if why := _held_back(ctx, "discovered", run):
        return why, None
    before = {d.symbol for d in discovered(ctx)}
    try:
        async with asyncio.timeout((cap_min or discover_cap_min(ctx)) * 60):
            run = await discover(ctx)
    except TimeoutError:
        added = [f"{d.symbol} {d.name} ({d.why})" for d in discovered(ctx) if d.symbol not in before]
        return "stopped at its time limit; " + (f"added {'; '.join(added)}" if added else "nothing added"), None
    except JobAlreadyRunning:
        return f"a discovery run is already going (Discover now): nothing more this {run}", None
    return _discovered_summary(run), run.source_errors[0] if run.source_errors else None


def questions_cap_min(ctx: AppContext, budget_min: float) -> float:
    """How long the questions stage may run: at most 15 minutes and a third of the budget."""
    return min(QUESTIONS_CAP_MIN, budget_min / 3)


async def _auto_questions(ctx: AppContext, cap_min: float, run: str) -> tuple[str, str | None]:
    """Auto questions answered again, adding what passes validation. Each stops validating at
    `cap_min` and no question starts without time for a typical answer; a hard stop one typical
    answer later catches a model that hangs."""
    if why := _held_back(ctx, "asked", run):
        return why, None
    started = ctx.clock.utcnow()
    deadline = started + timedelta(minutes=cap_min)
    try:
        async with asyncio.timeout((cap_min + TYPICAL_ANSWER_MIN) * 60):
            done = await answer_auto_questions(ctx, MAX_AUTO_QUESTIONS, deadline=deadline)
    except TimeoutError:
        return f"stopped at its time limit, added {', '.join(_auto_added(ctx, started)) or 'nothing'}", None
    if not done.answered and not done.failed:
        return f"nothing asked: {done.note}", None
    summary = (f"answered {len(done.answered)} auto question{'' if len(done.answered) == 1 else 's'}, "
               f"added {', '.join(_auto_added(ctx, started)) or 'nothing'}")
    if done.failed:
        summary += f", {len(done.failed)} failed"
    return summary + (f" ({done.note})" if done.note else ""), done.failed[0] if done.failed else None


def _auto_added(ctx: AppContext, since: datetime) -> list[str]:
    """What auto questions put on the discovered list from `since`, read back from the list so a
    stopped stage still says."""
    return [d.symbol for d in discovered(ctx) if d.relation == RELATION_AUTO and d.added_at
            and datetime.fromisoformat(d.added_at) >= since]


def _discovered_summary(run: DiscoveryRun) -> str:
    if run.added:
        return "added " + "; ".join(f"{a.symbol} {a.name} ({a.why})" for a in run.added)
    if run.note:
        return f"nothing added: {run.note}"
    where = f"nothing added from {', '.join(run.seeds)}"
    if not run.skipped:
        return f"{where}: no companies found"
    reasons = Counter(s.reason for s in run.skipped).most_common()
    return f"{where}: {len(run.skipped)} passed over ({', '.join(f'{n} {why}' for why, n in reasons)})"


async def _research(ctx: AppContext, say: Say, budget_min: float | None = None) -> tuple[str, str | None]:
    if not ctx.config.ai_account.enabled:
        return f"{OFF}: nothing researched", None
    done = await refresh_research(ctx, say, budget_min=budget_min)
    if not done.due:
        return "nothing due", None
    researched = f"{len(done.researched)} of {done.due} due researched"
    parts = [f"{researched} {UNVALIDATED_TAG}" if done.researched else researched]
    if done.failed:
        parts.append(f"{len(done.failed)} failed")
    if done.deferred:
        parts.append(f"{len(done.deferred)} deferred ({done.deferred_why})")
    return ", ".join(parts), ": ".join(done.failed[0]) if done.failed else None


async def _ai_step(ctx: AppContext, _: Say) -> tuple[str, str | None]:
    decisions = ai_step(ctx)
    if decisions is None:
        return f"{OFF}: nothing traded", None
    return f"{_decided(ctx, 'ai', decisions)} {UNVALIDATED_TAG}", None


def _decided(ctx: AppContext, account: str, decisions: list[Decision]) -> str:
    counts = Counter(d.action.value for d in decisions)
    # The rules say nothing about a listing they leave as it is.
    unchanged = len(set(traded_by(ctx, account)) - {d.symbol for d in decisions})
    return ", ".join(f"{counts[a]} {a}" for a in ("buy", "sell", "skip")) + f", {unchanged} no change"


STAGES: dict[str, StageRun] = {"prices": _prices, "fx": _fx, "fill": _fill, "analyse": _analyse,
                               "discover": _discover, "research": _research, "step": _step, "ai_step": _ai_step}
OFF = "AI account off"


def _midday_stages(started: datetime) -> dict[str, StageRun]:
    """Discovery and research only, sharing one budget from `started`: no prices, fills,
    analysis or steps, so it never trades."""
    def budget(ctx: AppContext) -> float:
        return ctx.config.schedule.midday_budget_min

    async def discover_stage(ctx: AppContext, say: Say) -> tuple[str, str | None]:
        return await _discover(ctx, say, discover_cap_min(ctx, budget(ctx)), MIDDAY_RUN)

    async def questions_stage(ctx: AppContext, _: Say) -> tuple[str, str | None]:
        return await _auto_questions(ctx, questions_cap_min(ctx, budget(ctx)), MIDDAY_RUN)

    async def research_stage(ctx: AppContext, say: Say) -> tuple[str, str | None]:
        return await _research(ctx, say, budget(ctx) - (ctx.clock.utcnow() - started) / timedelta(minutes=1))

    return {"discover": discover_stage, "questions": questions_stage, "research": research_stage}


MIDDAY_STAGES = ("discover", "questions", "research")
KIND_LABELS = {TICK: "Tick", MIDDAY: "Midday pass"}
MIDDAY_RUN = KIND_LABELS[MIDDAY].lower()


async def run_tick(ctx: AppContext, trigger: str = "manual",
                   on_stage: Callable[[str], None] | None = None) -> TickRun:
    """Run every stage once, in one process at a time, and store the outcome. `on_stage` is
    told each stage's name as it starts, then any progress the stage reports."""
    return await _run(ctx, TICK, trigger, STAGES, on_stage)


async def run_midday(ctx: AppContext, trigger: str = "manual",
                     on_stage: Callable[[str], None] | None = None) -> TickRun:
    """Discovery, then research on stale listings, under the tick's lock so the two never overlap."""
    return await _run(ctx, MIDDAY, trigger, _midday_stages(ctx.clock.utcnow()), on_stage)


async def _run(ctx: AppContext, kind: str, trigger: str, run_stages: dict[str, StageRun],
               on_stage: Callable[[str], None] | None) -> TickRun:
    started = ctx.clock.utcnow()
    stages: list[Stage] = []
    with single_flight(ctx, JOB, LEASE_S) as held:

        def say(text: str) -> None:
            # Progress doubles as the heartbeat, so a long research stage keeps the lock.
            held.check()
            if on_stage:
                on_stage(text)

        try:
            for name, stage in run_stages.items():
                say(name)
                stages.append(await _run_stage(ctx, name, stage, say))
        except BaseException as exc:
            # Stopped part way (server shutdown, lock taken over): still leave a record.
            _save(ctx, TickRun(trigger, started, ctx.clock.utcnow(), tuple(stages),
                               f"{INTERRUPTED}: {exc or type(exc).__name__}", kind=kind))
            raise
    return _save(ctx, TickRun(trigger, started, ctx.clock.utcnow(), tuple(stages), kind=kind))


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


def record_skipped(ctx: AppContext, trigger: str, note: str, at: datetime | None = None,
                   kind: str = TICK) -> TickRun:
    """A tick that never started, stored so the user can see why; dated `at` (a due time
    skipped ahead of time), or now."""
    at = at or ctx.clock.utcnow()
    return _save(ctx, TickRun(trigger, at, at, (), note, kind=kind))


def ticked_since(ctx: AppContext, at: datetime, kind: str = TICK) -> bool:
    """Whether any run of `kind` (skipped ones too) finished at or after `at`."""
    return TickRepo(ctx.conn).finished_since(at, kind)


def discovery_line(ctx: AppContext) -> str:
    """What discovery holds, which account trades it, and what the last tick's run did."""
    n = len(WatchlistRepo(ctx.conn).items(DISCOVERED_LIST))
    d = ctx.config.discovery
    trades = ("traded by the AI account" if d.ai_trades
              else "researched but not traded (the AI account's switch for them is in Settings)")
    line = (f"Discovery: {n} discovered compan{'y' if n == 1 else 'ies'}, {trades}; the strategy account and buy "
            "and hold keep to your own picks.")
    if not d.enabled:
        line += " Ticks do not discover: it is off in Settings."
    last = next(((r, s) for r in TickRepo(ctx.conn).recent() for s in r.stages if s.name == "discover"), None)
    return line + (f" Last {KIND_LABELS[last[0].kind].lower()}: {last[1].summary}." if last else "")


def last_tick(ctx: AppContext, kind: str = TICK) -> TickRun | None:
    found = TickRepo(ctx.conn).recent(1, kind)
    return found[0] if found else None


def last_midday(ctx: AppContext) -> TickRun | None:
    return last_tick(ctx, MIDDAY)
