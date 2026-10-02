"""Work started from the GUI, run as jobs: one queue per kind, so research runs one at a
time while a price update can run beside it, and every job is listed until the server
stops. Jobs are submitted from requests and run on the server's event loop."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from oracle.config import profile_tables
from oracle.jobs import pid_alive
from oracle.research.trace import RunStatus
from oracle.services.context import AppContext
from oracle.services.analysis_service import WatchedAnalysis, analyse_listing, analyse_watched
from oracle.services.backfill_service import backfill_universe
from oracle.services.backtest_service import check_backtest, keep_backtest, run_backtest
from oracle.services.data_check_service import run_data_checks
from oracle.services.cutoff_service import cannot_run, pinned_cutoff, probe_replay_models, replay_cutoff
from oracle.services.discovery_chat_service import (
    Question, answer, answer_progress, new_question, saved_question, sweep_interrupted_answers,
)
from oracle.services.discovery_service import discover
from oracle.services.errors import InvalidQuery, JobAlreadyRunning, ServiceError, UnknownJob
from oracle.services.job_executor import Executor, InlineExecutor, Work
from oracle.services.fx_service import check_sources, update_fx
from oracle.services.edges_service import find_edges, find_list_edges
from oracle.services.history_service import backfill_history
from oracle.services.industry_research_service import BUTTON_FRESH_H, research_industry
from oracle.services.laya_history_service import score_history
from oracle.services.laya_service import MODEL_TAG, score_company
from oracle.services.laya_test_service import MAIN, OWN, SENSITIVITY, check_test_model, keep_laya_test, run_laya_test
from oracle.services.reader_service import score_reader, waits_anywhere
from oracle.services.models_service import check_model_profile
from oracle.services.replay_score import update_study_prices
from oracle.services.research_service import (
    check_as_of, research, research_progress, stale_research, sweep_interrupted,
)
from oracle.services.signal_test_service import check_signal, run_and_keep
from oracle.services.testbed_service import build_testbed
from oracle.services.xbrl_service import fetch_fundamentals
from oracle.services.insider_service import fetch_insiders
from oracle.services.event_study_service import run_and_keep_events
from oracle.services.trend_service import run_and_keep_trend
from oracle.services.routine_service import update_listing_prices, update_prices
from oracle.services.paper_service import LINE_NAMES, REGIME_LINES
from oracle.services.tick_service import (
    FILL, KIND_LABELS, MIDDAY, RUNS, TICK, last_tick, record_skipped, run_line_step, ticked_since,
)
from oracle.services.universe_service import proposed_merges, refresh_universe, resolve_symbol
from oracle.services.watch_service import DEFAULT_LIST, watchlist_symbols
from oracle.store.job_history import INTERRUPTED, NOT_RUN, JobHistoryRepo, JobRow
from oracle.store.replay import FAILED, PENDING, ReplayRepo

__all__ = ["FinishedPage", "Job", "JobRunner", "PREEMPTED_BY", "RETRYABLE_STATES", "StaleQueued", "StaleResearch",
           "queue_stale_research", "retry", "retry_all", "retryable", "scheduled_run_near", "stale_research_jobs", "submit_analyse", "submit_ask",
           "nightly_data_check", "submit_backfill", "submit_backtest", "submit_cutoff_probe", "submit_data_check", "submit_discover", "submit_prices", "submit_reader", "submit_replay_prices",
           "submit_edges", "submit_fill", "submit_industry", "submit_history", "submit_laya", "submit_laya_history", "submit_laya_test", "submit_line_step", "submit_signal_test", "submit_xbrl", "submit_insiders", "submit_event_study", "submit_plan_trend", "submit_midday", "submit_rerun", "submit_research", "submit_tick", "submit_fx", "submit_merges",
           "submit_universe_refresh", "sweep_leftovers"]

log = logging.getLogger(__name__)

RECENT = 50
"""Finished jobs kept in memory, so their results can still be shown."""
PAGE_SIZE = 20
"""Finished jobs per page of the listing."""

PREEMPTED_BY = ("research", "tick", "ask", "discover", "industry")
"""Kinds of work that need the model: a replay study or a regime-AI backtest gives way to them."""
LIVE_WORK = (*PREEMPTED_BY, "prices")
"""Kinds of work the history backfill steps aside for: they read the same news and price sources."""
TICK_QUIET = timedelta(minutes=30)
"""How long before a scheduled run the history backfill stops asking, so the run has the sources."""
TICK_LATE = timedelta(minutes=15)
"""How long after a scheduled run's time the backfill still waits for it to be submitted."""
RETRYABLE_STATES = ("cancelled", "interrupted", "failed")
"""Finished states worth another try; `done` needs no retry."""
MISSED_LATE = "missed: started too late"


@dataclass(eq=False)
class Job:
    id: int
    kind: str
    label: str
    work: Work | None = field(default=None, repr=False)
    """None for a job read back from history."""
    subject: str | None = None
    """What it works on (a symbol), so the same thing is never queued twice."""
    link: str | None = None
    """Where its result shows in the GUI."""
    progress_fn: Callable[[], str | None] | None = field(default=None, repr=False)
    on_cancel: Callable[[], None] | None = field(default=None, repr=False)
    """Told the moment a running job is cancelled: work that never yields to its loop (a long
    replay on a worker thread) can only stop early by checking a flag this sets."""
    automatic: bool = False
    """Started by itself (a scheduled tick), not by a button: the automation switch can stop it."""
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    state: str = "queued"
    """queued, running, done, failed, cancelled, or interrupted (the app closed mid-run)."""
    error: str | None = None
    profile: str | None = None
    """The model profile picked for it, kept so a retry runs on the same one."""
    result: Any = field(default=None, repr=False)
    finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    def progress(self) -> str | None:
        """What a running job is doing now, when its kind can tell."""
        return self.progress_fn() if self.state == "running" and self.progress_fn else None


@dataclass(frozen=True)
class FinishedPage:
    items: list[Job]
    page: int
    pages: int
    total: int


class JobRunner:
    def __init__(self, ctx: AppContext, executor: Executor | None = None) -> None:
        """`executor` runs each job's work; by default on this loop (the GUI server passes a
        thread executor)."""
        self._ctx = ctx
        self._executor = executor or InlineExecutor(ctx)
        self._history = JobHistoryRepo(ctx.conn)
        self._queues: dict[str, deque[Job]] = {}
        self._running: dict[str, Job] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._finished: deque[Job] = deque(maxlen=RECENT)

    def submit(self, kind: str, label: str, work: Work, *, subject: str | None = None,
               link: str | None = None, progress: Callable[[], str | None] | None = None,
               automatic: bool = False, profile: str | None = None,
               on_cancel: Callable[[], None] | None = None) -> Job:
        """Queue `work`; a job of the same kind, subject and picked `profile` still waiting or
        running is returned instead of a second one."""
        if subject is not None:
            same = next((j for j in self.active(kind) if (j.subject, j.profile) == (subject, profile)), None)
            if same:
                return same
        now = self._ctx.clock.utcnow()
        job_id = self._history.add(kind, label, subject, link, "queued", now, os.getpid(), profile)
        job = Job(job_id, kind, label, work, subject=subject, link=link, progress_fn=progress, automatic=automatic,
                  submitted_at=now, profile=profile, on_cancel=on_cancel)
        self._queues.setdefault(kind, deque()).append(job)
        worker = self._workers.get(kind)
        if worker is None or worker.done():
            self._workers[kind] = asyncio.create_task(self._drain(kind))
        return job

    def current(self) -> list[Job]:
        """Running, then queued in order; finished jobs come a page at a time from `finished_page`."""
        return [*self._running.values(), *sorted(self._queued(), key=lambda j: j.id)]

    def finished_page(self, page: int, search: str = "") -> FinishedPage:
        """One page of finished jobs (earlier servers' too), newest first; a page past the
        end is the last one."""
        total = self.finished_count(search)
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(max(1, page), pages)
        here = {j.id: j for j in self._finished}
        rows = self._history.finished(PAGE_SIZE, (page - 1) * PAGE_SIZE, search)
        return FinishedPage([here.get(r.id) or _from_row(r) for r in rows], page, pages, total)

    def finished_count(self, search: str = "") -> int:
        return self._history.count_finished(search)

    def get(self, job_id: int) -> Job | None:
        live = [*self._running.values(), *self._queued(), *self._finished]
        found = next((j for j in live if j.id == job_id), None)
        if found is None and (row := self._history.get(job_id)) is not None:
            found = _from_row(row)
        return found

    async def wait(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job is None:
            raise UnknownJob(f"no job {job_id}")
        await job.finished.wait()
        return job

    def cancel(self, job_id: int) -> Job:
        """Drop a queued job, or cancel a running one; the queue then moves on."""
        job = self.get(job_id)
        if job is None:
            raise UnknownJob(f"no job {job_id}")
        if job.work is None and job.state in ("queued", "running"):
            raise InvalidQuery(f"{job.label} belongs to another copy of the app still running; stop it there")
        if job.state == "queued":
            self._queues[job.kind].remove(job)
            self._finish(job, "cancelled", "cancelled before it started")
        elif job.state == "running" and job.task is not None:
            _told(job)
            job.task.cancel()
        else:
            raise InvalidQuery(f"{job.label} is already {job.state}")
        return job

    def cancel_automatic(self) -> list[Job]:
        """Stop every automatic job queued or running here; work started by a button carries on."""
        going = [j for j in [*self._running.values(), *self._queued()] if j.automatic]
        for job in going:
            self.cancel(job.id)
        return going

    def active_labels(self) -> list[str]:
        """What is running or queued here, for a warning before the server stops."""
        return [j.label for j in [*self._running.values(), *self._queued()]]

    async def stop(self) -> None:
        """Cancel running work and drop what is queued; used when the server stops."""
        for queue in self._queues.values():
            while queue:
                self._finish(queue.popleft(), "cancelled", NOT_RUN)
        for job in self._running.values():
            _told(job)
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    def active(self, kind: str) -> list[Job]:
        """The kind's running job, then its queue."""
        running = self._running.get(kind)
        return ([running] if running else []) + list(self._queues.get(kind, ()))

    async def _drain(self, kind: str) -> None:
        queue = self._queues[kind]
        while queue:
            job = queue.popleft()
            job.state, job.started_at = "running", self._ctx.clock.utcnow()
            self._history.update(job.id, job.state, None, job.started_at, None)
            self._running[kind] = job
            # Its own task, so cancelling the job leaves this worker free for the next.
            job.task = asyncio.create_task(self._executor.run(job.work))
            try:
                job.result = await job.task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():  # the server is stopping
                    self._finish(job, "interrupted", INTERRUPTED)
                    raise
                self._finish(job, "cancelled", "cancelled")
            except Exception as exc:
                if not isinstance(exc, ServiceError):
                    log.exception("job %s failed", job.label)
                self._finish(job, "failed", str(exc) or type(exc).__name__)
            else:
                self._finish(job, "done", None)
            finally:
                self._running.pop(kind, None)

    def _queued(self) -> list[Job]:
        return [j for q in self._queues.values() for j in q]

    def _finish(self, job: Job, state: str, error: str | None) -> None:
        job.state, job.error, job.finished_at = state, error, self._ctx.clock.utcnow()
        self._history.update(job.id, state, error, job.started_at, job.finished_at)
        self._finished.append(job)
        job.finished.set()


def _told(job: Job) -> None:
    if job.on_cancel is not None:
        job.on_cancel()


def _from_row(r: JobRow) -> Job:
    job = Job(r.id, r.kind, r.label, subject=r.subject, link=r.link, submitted_at=r.submitted_at,
              started_at=r.started_at, finished_at=r.finished_at, state=r.state, error=r.error, profile=r.profile)
    job.finished.set()
    return job


def sweep_leftovers(ctx: AppContext) -> None:
    """At server start: close jobs and research runs that a server no longer running left
    queued or running, so none shows as going forever."""
    # Our own pid can only be an earlier runner in this process (or a reused pid): nothing
    # of this server's is queued yet.
    me = os.getpid()
    JobHistoryRepo(ctx.conn).sweep(ctx.clock.utcnow(), lambda pid: pid is None or pid == me or not pid_alive(pid))
    sweep_interrupted(ctx)
    sweep_interrupted_answers(ctx)


def submit_research(ctx: AppContext, runner: JobRunner, symbol: str, as_of: date | None = None,
                    profile: str | None = None, automatic: bool = False) -> Job:
    """Queue one research run, or with `as_of` a replay of that day, on the slots or every stage
    on `profile`; an unknown symbol, a future day or an unknown profile is refused now, not when
    its turn comes. `automatic`: started by the replay pacer, not a button."""
    listing = resolve_symbol(ctx, symbol).symbol
    check_as_of(ctx, as_of)
    check_model_profile(ctx, profile)

    async def work(c: AppContext):
        run = await research(c, listing, as_of=as_of, profile=profile)
        if run.status == RunStatus.FAILED:  # recorded on the run; Activity must agree with the company page
            raise ServiceError(run.error or "research failed")
        return run

    # A replay has its own subject, so it never joins (or is taken for) a live run of the listing.
    if as_of:
        subject, label = f"{listing}@{as_of.isoformat()}", f"Replay {listing} as of {as_of.isoformat()}"
    else:
        subject, label = listing, f"Research {listing}"
    return runner.submit("research", label + _on(profile), work, subject=subject, link=f"#company/{listing}",
                         progress=lambda: research_progress(ctx, listing), profile=profile, automatic=automatic)


def _on(profile: str | None) -> str:
    return f" on {profile}" if profile else ""


@dataclass(frozen=True)
class StaleResearch:
    due: list[str]
    """Every listing a tick would research now, in its order."""
    waiting: list[str]
    """Those already queued or running here."""

    @property
    def to_queue(self) -> list[str]:
        return [s for s in self.due if s not in self.waiting]


@dataclass(frozen=True)
class StaleQueued:
    jobs: list[Job]
    waiting: list[str]
    refused: list[tuple[str, str]]
    """Listing and why it could not be queued."""


def stale_research_jobs(ctx: AppContext, runner: JobRunner) -> StaleResearch:
    """The listings due for research by the tick's rule, and which of them are already queued."""
    due = stale_research(ctx)
    active = {j.subject for j in runner.active("research")}
    return StaleResearch(due, [s for s in due if s in active])


def queue_stale_research(ctx: AppContext, runner: JobRunner) -> StaleQueued:
    """One research job per due listing not already queued, back to back, with no time budget:
    someone asked for them all."""
    stale = stale_research_jobs(ctx, runner)
    jobs, refused = [], []
    for symbol in stale.to_queue:
        try:
            jobs.append(submit_research(ctx, runner, symbol))
        except ServiceError as exc:
            refused.append((symbol, str(exc)))
    return StaleQueued(jobs, stale.waiting, refused)


def submit_prices(ctx: AppContext, runner: JobRunner, symbol: str | None = None) -> Job:
    """Every watched listing, or one listing (watched or not)."""
    if symbol is None:
        return runner.submit("prices", "Update prices (watchlists)", update_prices, subject="*",
                             link="#watchlist")
    listing = resolve_symbol(ctx, symbol).symbol
    return runner.submit("prices", f"Update prices {listing}", lambda c: update_listing_prices(c, listing),
                         subject=listing, link=f"#company/{listing}")


def submit_replay_prices(ctx: AppContext, runner: JobRunner) -> Job:
    """Bars the replay study's score lacks, fetched for the listings that need them."""
    if ReplayRepo(ctx.conn).current() is None:
        raise InvalidQuery("no replay study yet: create one first")
    return runner.submit("replay_prices", "Update prices (replay study)", update_study_prices, subject="*",
                         link="#ledgers")


def submit_analyse(ctx: AppContext, runner: JobRunner, symbol: str | None = None) -> Job:
    if symbol is None:
        return runner.submit("analyse", "Analyse (watchlists)", analyse_watched, subject="*",
                             link="#watchlist")
    listing = resolve_symbol(ctx, symbol).symbol
    async def one(c: AppContext) -> list[WatchedAnalysis]:
        return [WatchedAnalysis(listing, await analyse_listing(c, listing), None)]

    return runner.submit("analyse", f"Analyse {listing}", one, subject=listing, link=f"#company/{listing}")


def submit_tick(ctx: AppContext, runner: JobRunner, trigger: str = "manual", skip_if_busy: bool = False,
                kind: str = TICK, due: datetime | None = None, deadline: datetime | None = None) -> Job:
    """One tick (or midday or fill pass) as a job; its progress is the stage it is on. With
    `skip_if_busy` (scheduled runs), one held by another process is recorded as skipped
    instead of failing. Every kind shares the tick queue, so they run one after the other.
    A scheduled run passes its `due` time: one run or skipped since it was queued is not
    run again. Past `deadline` as it starts, it is recorded as missed instead."""
    stage: list[str] = []
    run = RUNS[kind]

    async def work(c: AppContext):
        if due is not None and ticked_since(c, due, kind):
            return last_tick(c, kind)
        if deadline is not None and c.clock.utcnow() > deadline:
            return record_skipped(c, trigger, MISSED_LATE, kind=kind)
        try:
            return await run(c, trigger, on_stage=lambda name: stage.append(name))
        except JobAlreadyRunning as exc:
            if not skip_if_busy:
                raise
            return record_skipped(c, trigger, f"skipped: {exc}", kind=kind)

    return runner.submit("tick", f"{KIND_LABELS[kind]} ({trigger})", work, subject=kind, link="#watchlist",
                         progress=lambda: stage[-1] if stage else None, automatic=trigger != "manual")


def submit_line_step(ctx: AppContext, runner: JobRunner, account: str) -> Job:
    """A regime line's step as a job on the tick queue: it runs off the pages' event loop (a step
    takes seconds), after any tick queued before it and never beside one (gate 04 #1)."""
    if account not in REGIME_LINES:
        raise InvalidQuery(f"no regime line {account!r}; the lines are {', '.join(REGIME_LINES)}")

    async def work(c: AppContext):
        return run_line_step(c, account)

    return runner.submit("tick", f"{LINE_NAMES[account]} step", work, subject=account, link="#ledgers")


def submit_midday(ctx: AppContext, runner: JobRunner, trigger: str = "manual", skip_if_busy: bool = False) -> Job:
    return submit_tick(ctx, runner, trigger, skip_if_busy, MIDDAY)


def submit_fill(ctx: AppContext, runner: JobRunner, trigger: str = "manual", skip_if_busy: bool = False) -> Job:
    return submit_tick(ctx, runner, trigger, skip_if_busy, FILL)


def submit_universe_refresh(runner: JobRunner) -> Job:
    """Download and replace the universe, then queue the index backfill behind it; a second press
    joins the refresh already going."""
    stage: list[str] = []
    loop = asyncio.get_running_loop()

    async def work(c: AppContext):
        status = await refresh_universe(c, on_stage=stage.append)
        # The work may run on a worker thread; the runner lives on the loop's.
        try:
            loop.call_soon_threadsafe(submit_backfill, runner)
        except RuntimeError:
            pass  # the app closed while the refresh ran: it is saved, the backfill waits for the next
        return status

    return runner.submit("universe", "Refresh universe", work, subject="universe", link="#universe",
                         progress=lambda: stage[-1] if stage else None)


BACKFILL_SUBJECT = "backfill"


def submit_backfill(runner: JobRunner) -> Job:
    """Add the index members the universe lacks, a bounded batch a run. It shares the refresh's
    queue, so the two never run at once."""
    stage: list[str] = []
    return runner.submit("universe", "Backfill index members", lambda c: backfill_universe(c, stage.append),
                         subject=BACKFILL_SUBJECT, link="#universe", progress=lambda: stage[-1] if stage else None)


def submit_merges(runner: JobRunner) -> Job:
    """The companies suffix-blind grouping would join; reads only."""
    async def work(c: AppContext):
        return proposed_merges(c)

    return runner.submit("merges", "Proposed merges", work, subject="merges", link="#universe")


def submit_fx(runner: JobRunner, sources: Sequence[str] = ()) -> Job:
    """New rates for every currency stored prices use, from `sources` only when named."""
    names = list(dict.fromkeys(sources))
    check_sources(names)
    return runner.submit("fx", "Update FX" + (f" ({', '.join(names)})" if names else ""),
                         lambda c: update_fx(c, names or None), subject=",".join(names) or "*")


def submit_discover(runner: JobRunner) -> Job:
    """One discovery run: related companies, themes or market movers, onto the discovered list."""
    return runner.submit("discover", "Discover companies", discover, subject="discover", link="#watchlist")


def submit_ask(ctx: AppContext, runner: JobRunner, text: str, refines: int | None = None,
               profile: str | None = None) -> Job:
    """Save a question (or a follow-up refining `refines`) now, refusing a bad one, and answer it
    as a job, on the discovery slot or on `profile`."""
    check_model_profile(ctx, profile)
    return _submit_answer(ctx, runner, new_question(ctx, text, refines), profile)


def submit_rerun(ctx: AppContext, runner: JobRunner, question_id: int, profile: str | None = None) -> Job:
    """Answer a saved question again; the answer says what changed since the last."""
    check_model_profile(ctx, profile)
    return _submit_answer(ctx, runner, saved_question(ctx, question_id), profile)


def _submit_answer(ctx: AppContext, runner: JobRunner, q: Question, profile: str | None) -> Job:
    label = f"Ask: {q.text if len(q.text) <= 60 else q.text[:59] + '~'}{_on(profile)}"
    return runner.submit("ask", label, lambda c: answer(c, q.id, profile=profile), subject=str(q.id),
                         link=f"#discover/{q.id}", progress=lambda: answer_progress(ctx, q.id), profile=profile)


BACKTEST_SEP = "|"
"""Between the parts of a backtest job's subject, which a retry rebuilds the request from."""
WALK_FORWARD = "walk-forward"
"""The last part of a walk-forward backtest's subject; a plain one's has no such part."""


REGIME_AI_PART = "regime_ai"
"""The last part of a backtest subject that asked for the regime_ai line; a plain subject ends
in a number, so the two never join or mix up on retry."""


def submit_backtest(ctx: AppContext, runner: JobRunner, watchlist: str, start: date, end: date,
                    slippage_bps: float = 10.0, commission: float = 0.0, walk_forward: bool = False,
                    regime_ai: bool = False) -> Job:
    """Replay the strategy's rules over a list's stored prices and keep the result; bad days
    or costs are refused now. The same request pressed twice joins the first. `regime_ai` adds
    the replayed model tilt line, a model call per day (hours on a local model): the job says
    how far it is, and a Cancel stops the asking at once (the rest replays in seconds)."""
    name = check_backtest(ctx, watchlist, start, end, slippage_bps, commission, walk_forward)
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        b = run_backtest(c, name, start, end, slippage_bps, commission, walk_forward, regime_ai=regime_ai,
                         progress=stage.append if regime_ai else None, stop=stopped.is_set,
                         busy=lambda: _model_work(runner))
        # The replay never yields, so a Cancel pressed during it lands here, before the result is kept.
        await asyncio.sleep(0)
        return keep_backtest(c, b)

    parts = [name, start.isoformat(), end.isoformat(), repr(float(slippage_bps)), repr(float(commission))]
    label = f"Backtest {name}, {start.isoformat()} to {end.isoformat()}"
    if walk_forward:
        parts.append(WALK_FORWARD)
        label += ", walk-forward"
    if regime_ai:
        parts.append(REGIME_AI_PART)
        label += ", with regime AI"
    return runner.submit("backtest", label, work, subject=BACKTEST_SEP.join(parts), link="#ledgers",
                         progress=(lambda: stage[-1] if stage else None) if regime_ai else None,
                         on_cancel=stopped.set)


HISTORY_SUBJECT = "history"


def submit_history(ctx: AppContext, runner: JobRunner, watchlist: str = DEFAULT_LIST) -> Job:
    """The history backfill of `watchlist` and its industry maps as one long job (hours): its
    progress is the company and window it is on, it waits while live work runs, and a Cancel
    stops it between requests; what it finished is kept, so a retry resumes."""
    name = watchlist_symbols(ctx, watchlist)[0]
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        r = await backfill_history(c, watchlist=name, stop=stopped.is_set, busy=lambda: _history_waits(c, runner),
                                   on_progress=stage.append)
        # Ended early or with failures: failed in Activity, so it shows why and a Retry resumes it.
        if (r.stopped and r.stopped != "cancelled") or r.failures:
            raise ServiceError(r.summary)
        return r

    return runner.submit("history", f"Backfill history ({name} and industry maps)", work,
                         subject=f"{HISTORY_SUBJECT}{BACKTEST_SEP}{name}", link="#ledgers",
                         progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


TESTBED_SUBJECT = "testbed"
TESTBED_REFRESH = "refresh"
TESTBED_VERIFY = "verify"
# Other Yahoo-heavy work too: the testbed paces itself on the research database, not the live one.
TESTBED_WAITS = (*LIVE_WORK, "history", "replay_prices", "universe")


def submit_testbed(ctx: AppContext, runner: JobRunner, refresh: bool = False, verify: bool = False) -> Job:
    """Build the signal testbed (or with `refresh` re-read its membership and top up current
    members, with `verify` check renames and reused tickers again) as one long job: it waits while
    live work runs, a Cancel stops it between requests, and what it fetched is kept, so a retry resumes."""
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        r = await build_testbed(c, refresh=refresh, verify=verify, stop=stopped.is_set,
                                busy=lambda: _testbed_waits(c, runner), on_progress=stage.append)
        if (r.stopped and r.stopped != "cancelled") or r.failures:
            raise ServiceError(r.summary)
        return r

    modes = [m for m, on in ((TESTBED_REFRESH, refresh), (TESTBED_VERIFY, verify)) if on]
    title = ("Refresh the signal testbed" if refresh else "Build the signal testbed") + (
        " and check its renames" if verify else "")
    return runner.submit("testbed", title, work, subject=BACKTEST_SEP.join([TESTBED_SUBJECT, *modes]),
                         link="#ledgers", progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


def _testbed_waits(ctx: AppContext, runner: JobRunner) -> str | None:
    return _live_work(runner, TESTBED_WAITS) or scheduled_run_near(ctx)


def _retry_testbed(ctx: AppContext, runner: JobRunner, job) -> Job:
    modes = (job.subject or "").split(BACKTEST_SEP)[1:]
    return submit_testbed(ctx, runner, TESTBED_REFRESH in modes, TESTBED_VERIFY in modes)


XBRL_SUBJECT = "xbrl"
XBRL_WAITS = (*TESTBED_WAITS, "testbed")
"""It reads the testbed's members, and the live research reads SEC too."""


def submit_xbrl(ctx: AppContext, runner: JobRunner, refresh: bool = False) -> Job:
    """Fetch the testbed members' SEC XBRL facts, filing times and splits into the research database
    (about an hour the first time; with `refresh`, every member again). It waits while live work or a
    testbed build runs, a Cancel stops it between requests, and what it fetched is kept."""
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        r = await fetch_fundamentals(c, refresh=refresh, stop=stopped.is_set,
                                     busy=lambda: _live_work(runner, XBRL_WAITS) or scheduled_run_near(c),
                                     on_progress=stage.append)
        if (r.stopped and r.stopped != "cancelled") or r.failures:
            raise ServiceError(r.summary)
        return r

    return runner.submit("xbrl", "Refetch SEC fundamentals" if refresh else "Fetch SEC fundamentals", work,
                         subject=XBRL_SUBJECT + (BACKTEST_SEP + TESTBED_REFRESH if refresh else ""),
                         link="#ledgers", progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


def _retry_xbrl(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_xbrl(ctx, runner, (job.subject or "").endswith(BACKTEST_SEP + TESTBED_REFRESH))


INSIDERS_SUBJECT = "insiders"


def submit_insiders(ctx: AppContext, runner: JobRunner, refresh: bool = False) -> Job:
    """Fetch the testbed members' Form 4 trades (SEC's quarterly data sets) and their Form 4 and 8-K
    filing times and items into the research database (with `refresh`, all again). It waits like the
    fundamentals fetch, a Cancel stops it between requests, and what it fetched is kept."""
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        r = await fetch_insiders(c, refresh=refresh, stop=stopped.is_set,
                                 busy=lambda: _live_work(runner, (*XBRL_WAITS, "xbrl")) or scheduled_run_near(c),
                                 on_progress=stage.append)
        if (r.stopped and r.stopped != "cancelled") or r.failures:
            raise ServiceError(r.summary)
        return r

    return runner.submit("insiders", "Refetch Form 4s and 8-Ks" if refresh else "Fetch Form 4s and 8-Ks", work,
                         subject=INSIDERS_SUBJECT + (BACKTEST_SEP + TESTBED_REFRESH if refresh else ""),
                         link="#ledgers", progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


def _retry_insiders(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_insiders(ctx, runner, (job.subject or "").endswith(BACKTEST_SEP + TESTBED_REFRESH))


SIGNAL_TEST_WAITS = (*TESTBED_WAITS, "testbed", "xbrl", "insiders")
"""A test run during a build or a fetch would read a half-filled testbed."""
EVENT_STUDY_SUBJECT = "event_study"


def submit_event_study(ctx: AppContext, runner: JobRunner, train_only: bool = False) -> Job:
    """The pre-registered typed-event study on the testbed (with `train_only`, 2010-2019 alone), kept
    in the research database. It reads only and waits like a signal test; a Cancel keeps nothing."""
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        return await run_and_keep_events(c, train_only=train_only, stop=stopped.is_set,
                                         busy=lambda: _signal_test_waits(c, runner), on_progress=stage.append)

    return runner.submit("event_study", "Event study" + (", train only" if train_only else ""), work,
                         subject=EVENT_STUDY_SUBJECT + (BACKTEST_SEP + TRAIN_PART if train_only else ""),
                         link="#ledgers", progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


def _retry_event_study(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_event_study(ctx, runner, (job.subject or "").endswith(BACKTEST_SEP + TRAIN_PART))
TRAIN_PART = "train"


PLAN_TREND_SUBJECT = "plan_trend"


def submit_plan_trend(ctx: AppContext, runner: JobRunner) -> Job:
    """The pre-registered trend overlay test: fetch the newest ^SP500TR days into the research
    database, run both windows and keep the result there (seconds)."""
    stage: list[str] = []

    async def work(c: AppContext):
        return await run_and_keep_trend(c, say=stage.append)

    return runner.submit("plan_trend", "Trend overlay test", work, subject=PLAN_TREND_SUBJECT, link="#ledgers",
                         progress=lambda: stage[-1] if stage else None)


def _retry_plan_trend(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_plan_trend(ctx, runner)


DATA_CHECK = "data_check"


def submit_data_check(ctx: AppContext, runner: JobRunner, automatic: bool = False) -> Job:
    """The data-error checks over the live and research databases (a minute or so), kept as the
    newest run; flags only, never an edit. A second press joins the run already queued."""
    stage: list[str] = []

    async def work(c: AppContext):
        return await run_data_checks(c, say=stage.append)

    return runner.submit(DATA_CHECK, "Data checks", work, subject=DATA_CHECK, link="#ledgers",
                         progress=lambda: stage[-1] if stage else None, automatic=automatic)


def nightly_data_check(ctx: AppContext, runner: JobRunner) -> Job | None:
    """After a scheduled tick: the checks as an automatic job, unless `[data_checks] nightly` or
    the automation switch is off."""
    if not (ctx.config.data_checks.nightly and ctx.config.automation.enabled):
        return None
    return submit_data_check(ctx, runner, automatic=True)


def _retry_data_check(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_data_check(ctx, runner)


def submit_signal_test(ctx: AppContext, runner: JobRunner, name: str, train_only: bool = False) -> Job:
    """The pre-registered signal test of `name` on the testbed (with `train_only`, 2010-2019 alone,
    confirm never read), kept in the research database (minutes). It reads only, waits while live
    work or a testbed build runs, and a Cancel stops it between chunks of days, keeping nothing."""
    check_signal(name)
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        return await run_and_keep(c, name, train_only=train_only, stop=stopped.is_set,
                                  busy=lambda: _signal_test_waits(c, runner), on_progress=stage.append)

    label = f"Signal test: {name}" + (", train only" if train_only else "")
    return runner.submit("signal_test", label, work, subject=name + (BACKTEST_SEP + TRAIN_PART if train_only else ""),
                         link="#ledgers", progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


def _signal_test_waits(ctx: AppContext, runner: JobRunner) -> str | None:
    return _live_work(runner, SIGNAL_TEST_WAITS) or scheduled_run_near(ctx)


def _retry_signal_test(ctx: AppContext, runner: JobRunner, job) -> Job:
    subject = job.subject or ""
    train_only = subject.endswith(BACKTEST_SEP + TRAIN_PART)
    return submit_signal_test(ctx, runner, subject.removesuffix(BACKTEST_SEP + TRAIN_PART), train_only)


def submit_laya(ctx: AppContext, runner: JobRunner, symbol: str) -> Job:
    """Score the company's stored articles with Laya, those its model has not scored yet. It waits
    while research, a tick, a question or discovery runs (their model shares the GPU), and a
    Cancel stops it between batches, keeping what was scored."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    listing = resolve_symbol(ctx, symbol).symbol
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        return score_company(c, listing, stop=stopped.is_set, busy=lambda: _model_work(runner), on_progress=stage.append)

    return runner.submit("laya", f"Laya {listing}", work, subject=listing, link=f"#company/{listing}",
                         progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


LAYA_CPU = "cpu"
"""The last part of a Laya history subject that allowed the CPU."""
_BUT_INDUSTRY = tuple(k for k in PREEMPTED_BY if k != "industry")
"""What an industry job gives way to: never itself."""


def submit_laya_history(ctx: AppContext, runner: JobRunner, watchlist: str = DEFAULT_LIST,
                        allow_cpu: bool = False) -> Job:
    """Score the backfilled history of `watchlist` and its industry maps with Laya per (article,
    target) pair (hours). It waits while research, a tick, a question, discovery or industry
    research runs here or elsewhere, closing the model, and for GPU room unless `allow_cpu`; a
    Cancel stops it between batches; a retry resumes where it stopped."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    name = watchlist_symbols(ctx, watchlist)[0]
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        r = score_history(c, name, allow_cpu=allow_cpu, stop=stopped.is_set, busy=lambda: _laya_history_waits(runner),
                          on_progress=stage.append)
        if r.stopped and r.stopped != "cancelled":
            raise ServiceError(r.summary)
        return r

    label = f"Laya on history ({name} and industry maps)" + (", CPU allowed" if allow_cpu else "")
    subject = BACKTEST_SEP.join((name, LAYA_CPU)) if allow_cpu else name
    return runner.submit("laya_history", label, work, subject=subject,
                         link="#ledgers", progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


def submit_laya_test(ctx: AppContext, runner: JobRunner, sensitivity: bool = False, watchlist: str = DEFAULT_LIST,
                     model: str = MODEL_TAG, own_only: bool = False) -> Job:
    """Run the pre-registered predictive test (or its sensitivity or own-news-only run) over
    `model`'s scored history, keeping the result for the Backtest page. Reads only."""
    if sensitivity and own_only:
        raise InvalidQuery("the sensitivity run is about map links; it cannot be own news only")
    name = watchlist_symbols(ctx, watchlist)[0]
    check_test_model(ctx, model)
    run = OWN if own_only else SENSITIVITY if sensitivity else MAIN
    stage: list[str] = []

    async def work(c: AppContext):
        return keep_laya_test(c, run_laya_test(c, name, sensitivity=sensitivity, own_only=own_only, model=model,
                                               on_progress=stage.append))

    who = "" if model == MODEL_TAG else f", {model}"
    return runner.submit("laya_test", f"Laya predictive test ({run}, {name}{who})", work,
                         subject=BACKTEST_SEP.join((run, name, model)), link="#ledgers",
                         progress=lambda: stage[-1] if stage else None)


READER_SUBJECT = "reader"
OWN_PART = "own"
"""The last part of an own-news-only reader job's subject."""


def submit_reader(ctx: AppContext, runner: JobRunner, watchlist: str = DEFAULT_LIST, own_only: bool = False) -> Job:
    """The stronger reader over the history backfill of `watchlist` (hours; `own_only`: the list's own
    news, not its map companies'): each headline read once by the local model for its own company,
    pairs stored from that reading. It waits while model work runs anywhere or a scheduled run is due
    within 30 minutes, and a Cancel stops it between batches; what it read is kept, so a retry
    resumes."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    name = watchlist_symbols(ctx, watchlist)[0]
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        return score_reader(c, name, own_only=own_only, stop=stopped.is_set, busy=lambda: _reader_waits(c, runner),
                            on_progress=stage.append)

    scope = "own news" if own_only else "history"
    subject = BACKTEST_SEP.join((READER_SUBJECT, name, *((OWN_PART,) if own_only else ())))
    return runner.submit("reader", f"Stronger reader ({ctx.config.laya.reader_model}) over {name}'s {scope}", work,
                         subject=subject, link="#ledgers", progress=lambda: stage[-1] if stage else None,
                         on_cancel=stopped.set)


EDGES_LIST = "list:"
"""A ties job's subject for a whole list, beside a symbol's."""


def submit_edges(ctx: AppContext, runner: JobRunner, symbol: str | None, watchlist: str | None = None) -> Job:
    """Read a company's (or every listed company's) headlines and 10-Ks for ties to other companies,
    typed by the keyword rule and Laya. It waits while research, a tick, a question or discovery
    runs, and a Cancel stops it between companies, keeping what was read."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    if (symbol is None) == (watchlist is None):
        raise InvalidQuery("give a symbol or a list")
    listing = resolve_symbol(ctx, symbol).symbol if symbol else None
    stage: list[str] = []
    stopped = threading.Event()
    kw = dict(stop=stopped.is_set, busy=lambda: _model_work(runner), on_progress=stage.append)

    async def work(c: AppContext):
        if listing:
            return await find_edges(c, [listing], **kw)
        return await find_list_edges(c, watchlist, **kw)

    label = f"Find ties for {listing}" if listing else f"Find ties for list {watchlist}"
    return runner.submit("edges", label, work, subject=listing or EDGES_LIST + watchlist,
                         link=f"#company/{listing}" if listing else "#watchlist",
                         progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


INDUSTRY_DAILY = "daily"
"""An industry job's subject for the tick's daily set, beside a symbol's."""


def submit_industry(ctx: AppContext, runner: JobRunner, symbol: str | None = None) -> Job:
    """Research industry: search the company's (or the daily set's) and its map companies' new
    headlines and score them with Laya per related pair, updating its daily features. It waits
    while research, a tick, a question or discovery runs, and a Cancel stops it between searches
    or batches, keeping what was done."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    listing = resolve_symbol(ctx, symbol).symbol if symbol else None
    stage: list[str] = []
    stopped = threading.Event()

    async def work(c: AppContext):
        return await research_industry(c, [listing] if listing else None, fresh_h=BUTTON_FRESH_H,
                                       stop=stopped.is_set, busy=lambda: _live_work(runner, _BUT_INDUSTRY),
                                       on_progress=stage.append)

    label = f"Research industry {listing}" if listing else "Research industry (held, watched and main list)"
    return runner.submit("industry", label, work, subject=listing or INDUSTRY_DAILY,
                         link=f"#company/{listing}" if listing else "#activity",
                         progress=lambda: stage[-1] if stage else None, on_cancel=stopped.set)


def submit_cutoff_probe(ctx: AppContext, runner: JobRunner, profile: str | None = None) -> Job:
    """Ask `profile`'s model, or each unprobed model a replay runs on, the dated questions and keep
    their measured cutoffs; an unknown profile is refused now."""
    replay_cutoff(ctx, profile)
    stage: list[str] = []
    return runner.submit("probe", "Probe model cutoff" + _on(profile),
                         lambda c: probe_replay_models(c, profile, on_progress=stage.append), subject="cutoff",
                         link="#ledgers", progress=lambda: stage[-1] if stage else None, profile=profile)


# One resubmit per kind, rebuilding the call from what a finished job (live or from history,
# both `Job` and `JobRow` carry kind/subject/label) stored about itself. A kind missing here
# cannot be retried: nothing kept enough to rebuild its call.
def _retry_research(ctx: AppContext, runner: JobRunner, job) -> Job:
    subject = job.subject or ""
    if "@" in subject:
        symbol, day = subject.split("@", 1)
        if why := _refused_replay(ctx, job):
            raise InvalidQuery(why)
        again = submit_research(ctx, runner, symbol, date.fromisoformat(day), job.profile)
        _adopt_replay_cell(ctx, again, symbol, date.fromisoformat(day))
        return again
    return submit_research(ctx, runner, subject, profile=job.profile)


def _study_cell(ctx: AppContext, job, symbol: str, day: date):
    """The current study's cell a replay job stands for; a run on a picked profile is not one."""
    if job.profile is not None:
        return None
    repo = ReplayRepo(ctx.conn)
    study = repo.current()
    return repo.cell_at(study.id, symbol, day) if study else None


def _refused_replay(ctx: AppContext, job) -> str | None:
    """Why a replay job of a study cell may not run again: the study's models changed, its day
    is now on or before the cutoff, or the cutoff allows none."""
    symbol, day = (job.subject or "").split("@", 1)
    if _study_cell(ctx, job, symbol, date.fromisoformat(day)) is None:
        return None
    stages = ReplayRepo(ctx.conn).current().stages
    why, _ = cannot_run(ctx, stages)
    if why:
        return why
    cutoff = pinned_cutoff(ctx, stages)
    if cutoff and date.fromisoformat(day) <= cutoff:
        return f"{day} is on or before the measured cutoff {cutoff}: a replay then could see ahead"
    return None


def _adopt_replay_cell(ctx: AppContext, job: Job, symbol: str, day: date) -> None:
    """A retried replay of a study cell that failed or waits for its retry counts for that
    cell, so the study never runs it a second time. The study replays on the slots, so a run on
    a picked profile is another experiment."""
    cell = _study_cell(ctx, job, symbol, day)
    if cell and cell.state in (PENDING, FAILED):
        ReplayRepo(ctx.conn).adopt(cell.id, job.id, ctx.clock.utcnow())


def _retry_prices(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_prices(ctx, runner, None if job.subject == "*" else job.subject)


def _retry_replay_prices(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_replay_prices(ctx, runner)


def _retry_analyse(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_analyse(ctx, runner, None if job.subject == "*" else job.subject)


def _retry_tick(ctx: AppContext, runner: JobRunner, job) -> Job:
    """Whatever started it before (scheduled or manual), a retry is asked for: a manual run of the same
    kind, or the same regime line's step."""
    if job.subject in REGIME_LINES:
        return submit_line_step(ctx, runner, job.subject)
    return submit_tick(ctx, runner, kind=job.subject if job.subject in RUNS else TICK)


def _retry_universe(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_backfill(runner) if job.subject == BACKFILL_SUBJECT else submit_universe_refresh(runner)


def _retry_merges(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_merges(runner)


def _retry_discover(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_discover(runner)


def _retry_ask(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_rerun(ctx, runner, int(job.subject), job.profile)


def _retry_fx(ctx: AppContext, runner: JobRunner, job) -> Job:
    sources = [] if job.subject in (None, "*", "") else job.subject.split(",")
    return submit_fx(runner, sources)


def _model_work(runner: JobRunner) -> str | None:
    """The label of running work that needs the model, which a regime-AI backtest waits for, as
    the replay study does."""
    return _live_work(runner, PREEMPTED_BY)


def _live_work(runner: JobRunner, kinds: Sequence[str]) -> str | None:
    """The label of running work of `kinds`. Read from a worker thread, so a queue changing under
    the read is taken as nothing found; the next look catches it."""
    try:
        return next((j.label for j in list(runner.current()) if j.state == "running" and j.kind in kinds), None)
    except RuntimeError:
        return None


def _history_waits(ctx: AppContext, runner: JobRunner) -> str | None:
    """Live work running, or a scheduled tick or midday pass due within TICK_QUIET or due and not
    yet run: the backfill leaves Google News and Yahoo to it until it has run. Read on the job's
    own connection, so it is safe from the worker thread."""
    return _live_work(runner, LIVE_WORK) or scheduled_run_near(ctx)


def _reader_waits(ctx: AppContext, runner: JobRunner) -> str | None:
    """Model work in this app or any process, or a scheduled run due within TICK_QUIET (or due and
    not yet run): the stronger reader leaves Ollama to it."""
    return _laya_history_waits(runner) or waits_anywhere(ctx)


def _laya_history_waits(runner: JobRunner) -> str | None:
    """In-app work Laya's history scoring and the stronger reader give way to: model work, and a
    regime-AI backtest (it asks Ollama's model every replayed day)."""
    if live := _model_work(runner):
        return live
    try:
        return next((j.label for j in list(runner.current()) if j.state == "running" and j.kind == "backtest"
                     and (j.subject or "").endswith(BACKTEST_SEP + REGIME_AI_PART)), None)
    except RuntimeError:
        return None


def scheduled_run_near(ctx: AppContext) -> str | None:
    """A scheduled tick or midday pass due within TICK_QUIET, or due and not yet run, by name."""
    # Imported here: the schedule module imports this one.
    from oracle.services.schedule_service import last_due, next_due

    s = ctx.config.schedule
    if not (s.enabled and ctx.config.automation.enabled):
        return None
    now = ctx.clock.utcnow()
    for kind, hhmm in ((TICK, s.time), (MIDDAY, s.midday_time)):
        if not hhmm:
            continue
        if (due := next_due(now, hhmm)) - now <= TICK_QUIET:
            return f"the scheduled {KIND_LABELS[kind].lower()} at {due:%H:%M} UTC"
        if now - (due := last_due(now, hhmm)) <= TICK_LATE and not ticked_since(ctx, due, kind):
            return f"the scheduled {KIND_LABELS[kind].lower()} due at {due:%H:%M} UTC"
    return None


def _retry_reader(ctx: AppContext, runner: JobRunner, job) -> Job:
    subject = (job.subject or "").removeprefix(READER_SUBJECT + BACKTEST_SEP)
    own = subject.endswith(BACKTEST_SEP + OWN_PART)
    return submit_reader(ctx, runner, subject.removesuffix(BACKTEST_SEP + OWN_PART), own)


def _retry_history(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_history(ctx, runner, (job.subject or "").split(BACKTEST_SEP, 1)[-1])


def _retry_backtest(ctx: AppContext, runner: JobRunner, job) -> Job:
    # From the right: the other parts never hold the separator, a list name might.
    subject = job.subject or ""
    ai = subject.endswith(BACKTEST_SEP + REGIME_AI_PART)
    subject = subject.removesuffix(BACKTEST_SEP + REGIME_AI_PART)
    walk = subject.endswith(BACKTEST_SEP + WALK_FORWARD)
    name, start, end, slippage, commission = subject.removesuffix(BACKTEST_SEP + WALK_FORWARD).rsplit(BACKTEST_SEP, 4)
    return submit_backtest(ctx, runner, name, date.fromisoformat(start), date.fromisoformat(end), float(slippage),
                           float(commission), walk, ai)


def _retry_laya(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_laya(ctx, runner, job.subject)


def _retry_laya_history(ctx: AppContext, runner: JobRunner, job) -> Job:
    subject = job.subject or DEFAULT_LIST
    cpu = subject.endswith(BACKTEST_SEP + LAYA_CPU)
    return submit_laya_history(ctx, runner, subject.removesuffix(BACKTEST_SEP + LAYA_CPU), cpu)


def _retry_laya_test(ctx: AppContext, runner: JobRunner, job) -> Job:
    run, name, model = (job.subject or "").split(BACKTEST_SEP, 2)
    return submit_laya_test(ctx, runner, run == SENSITIVITY, name, model, run == OWN)


def _retry_edges(ctx: AppContext, runner: JobRunner, job) -> Job:
    subject = job.subject or ""
    if subject.startswith(EDGES_LIST):
        return submit_edges(ctx, runner, None, subject.removeprefix(EDGES_LIST))
    return submit_edges(ctx, runner, subject)


def _retry_industry(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_industry(ctx, runner, None if job.subject == INDUSTRY_DAILY else job.subject)


def _retry_probe(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_cutoff_probe(ctx, runner, job.profile)


_RETRY: dict[str, Callable[[AppContext, JobRunner, Any], Job]] = {
    "research": _retry_research, "prices": _retry_prices, "analyse": _retry_analyse, "tick": _retry_tick,
    "universe": _retry_universe, "merges": _retry_merges, "fx": _retry_fx, "discover": _retry_discover,
    "backtest": _retry_backtest, "ask": _retry_ask, "replay_prices": _retry_replay_prices, "probe": _retry_probe,
    "history": _retry_history, "testbed": _retry_testbed, "signal_test": _retry_signal_test, "xbrl": _retry_xbrl,
    "insiders": _retry_insiders, "event_study": _retry_event_study, "plan_trend": _retry_plan_trend,
    DATA_CHECK: _retry_data_check,
    "laya": _retry_laya, "edges": _retry_edges, "industry": _retry_industry, "reader": _retry_reader,
    "laya_history": _retry_laya_history, "laya_test": _retry_laya_test,
}


def retry(ctx: AppContext, runner: JobRunner, job_id: int) -> Job:
    """Re-submit a finished job of a retryable kind with the same subject and arguments."""
    job = runner.get(job_id)
    if job is None:
        raise UnknownJob(f"no job {job_id}")
    if job.state not in RETRYABLE_STATES:
        raise InvalidQuery(f"{job.label} is {job.state}, not cancelled, interrupted or failed")
    resubmit = _RETRY.get(job.kind)
    if resubmit is None:
        raise InvalidQuery(f"{job.label} cannot be retried: its kind is not one this app rebuilds")
    return resubmit(ctx, runner, job)


def retryable(ctx: AppContext, runner: JobRunner) -> list[JobRow]:
    """Every finished job that ended cancelled, interrupted or failed, still needs it (no
    later run of the same kind and subject succeeded since), is not already queued or
    running, and is a kind this app can rebuild on a profile that still exists. Not a regime-AI
    backtest: hours of model calls are retried one at a time, on purpose. The newest
    run is found by subject alone, whatever profile it ran on: a listing researched since on
    another profile needs no retry."""
    out = []
    profiles = profile_tables(ctx.config)
    for row in JobHistoryRepo(ctx.conn).latest_per_subject():
        if row.state not in RETRYABLE_STATES or row.kind not in _RETRY:
            continue
        if row.profile is not None and row.profile not in profiles:
            continue
        if any(j.subject == row.subject for j in runner.active(row.kind)):
            continue
        if row.kind == "research" and "@" in (row.subject or "") and _refused_replay(ctx, row):
            continue
        if row.kind == "backtest" and (row.subject or "").endswith(BACKTEST_SEP + REGIME_AI_PART):
            continue
        out.append(row)
    return out


def retry_all(ctx: AppContext, runner: JobRunner) -> list[Job]:
    """Re-submit every job `retryable` finds, e.g. after a restart cancelled a batch at once."""
    return [_RETRY[row.kind](ctx, runner, row) for row in retryable(ctx, runner)]
