"""Work started from the GUI, run as jobs: one queue per kind, so research runs one at a
time while a price update can run beside it, and every job is listed until the server
stops. Jobs are submitted from requests and run on the server's event loop."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from oracle.config import profile_tables
from oracle.jobs import pid_alive
from oracle.research.trace import RunStatus
from oracle.services.context import AppContext
from oracle.services.analysis_service import WatchedAnalysis, analyse_listing, analyse_watched
from oracle.services.backtest_service import check_backtest, keep_backtest, run_backtest
from oracle.services.discovery_chat_service import (
    Question, answer, answer_progress, new_question, saved_question, sweep_interrupted_answers,
)
from oracle.services.discovery_service import discover
from oracle.services.errors import InvalidQuery, JobAlreadyRunning, ServiceError, UnknownJob
from oracle.services.job_executor import Executor, InlineExecutor, Work
from oracle.services.fx_service import check_sources, update_fx
from oracle.services.models_service import check_model_profile
from oracle.services.research_service import (
    check_as_of, research, research_progress, stale_research, sweep_interrupted,
)
from oracle.services.routine_service import update_listing_prices, update_prices
from oracle.services.tick_service import (
    KIND_LABELS, MIDDAY, TICK, last_tick, record_skipped, run_midday, run_tick, ticked_since,
)
from oracle.services.universe_service import proposed_merges, refresh_universe, resolve_symbol
from oracle.store.job_history import INTERRUPTED, NOT_RUN, JobHistoryRepo, JobRow
from oracle.store.replay import FAILED, PENDING, ReplayRepo

__all__ = ["FinishedPage", "Job", "JobRunner", "RETRYABLE_STATES", "StaleQueued", "StaleResearch",
           "queue_stale_research", "retry", "retry_all", "retryable", "stale_research_jobs", "submit_analyse", "submit_ask",
           "submit_backtest", "submit_discover", "submit_prices", "submit_midday", "submit_rerun", "submit_research",
           "submit_tick", "submit_fx", "submit_merges", "submit_universe_refresh", "sweep_leftovers"]

log = logging.getLogger(__name__)

RECENT = 50
"""Finished jobs kept in memory, so their results can still be shown."""
PAGE_SIZE = 20
"""Finished jobs per page of the listing."""

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
               automatic: bool = False, profile: str | None = None) -> Job:
        """Queue `work`; a job of the same kind, subject and picked `profile` still waiting or
        running is returned instead of a second one."""
        if subject is not None:
            same = next((j for j in self.active(kind) if (j.subject, j.profile) == (subject, profile)), None)
            if same:
                return same
        now = self._ctx.clock.utcnow()
        job_id = self._history.add(kind, label, subject, link, "queued", now, os.getpid(), profile)
        job = Job(job_id, kind, label, work, subject=subject, link=link, progress_fn=progress, automatic=automatic,
                  submitted_at=now, profile=profile)
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
    """One tick (or midday pass) as a job; its progress is the stage it is on. With
    `skip_if_busy` (scheduled runs), one held by another process is recorded as skipped
    instead of failing. Both kinds share the tick queue, so they run one after the other.
    A scheduled run passes its `due` time: one run or skipped since it was queued is not
    run again. Past `deadline` as it starts, it is recorded as missed instead."""
    stage: list[str] = []
    run = run_midday if kind == MIDDAY else run_tick

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


def submit_midday(ctx: AppContext, runner: JobRunner, trigger: str = "manual", skip_if_busy: bool = False) -> Job:
    return submit_tick(ctx, runner, trigger, skip_if_busy, MIDDAY)


def submit_universe_refresh(runner: JobRunner) -> Job:
    """Download and replace the universe; a second press joins the refresh already going."""
    stage: list[str] = []
    return runner.submit("universe", "Refresh universe", lambda c: refresh_universe(c, on_stage=stage.append),
                         subject="universe", link="#universe", progress=lambda: stage[-1] if stage else None)


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


def submit_backtest(ctx: AppContext, runner: JobRunner, watchlist: str, start: date, end: date,
                    slippage_bps: float = 10.0, commission: float = 0.0) -> Job:
    """Replay the strategy's rules over a list's stored prices and keep the result; bad days
    or costs are refused now. The same request pressed twice joins the first."""
    name = check_backtest(ctx, watchlist, start, end, slippage_bps, commission)

    async def work(c: AppContext):
        b = run_backtest(c, name, start, end, slippage_bps, commission)
        # The replay never yields, so a Cancel pressed during it lands here, before the result is kept.
        await asyncio.sleep(0)
        return keep_backtest(c, b)

    subject = BACKTEST_SEP.join([name, start.isoformat(), end.isoformat(), repr(float(slippage_bps)),
                                 repr(float(commission))])
    return runner.submit("backtest", f"Backtest {name}, {start.isoformat()} to {end.isoformat()}", work,
                         subject=subject, link="#ledgers")


# One resubmit per kind, rebuilding the call from what a finished job (live or from history,
# both `Job` and `JobRow` carry kind/subject/label) stored about itself. A kind missing here
# cannot be retried: nothing kept enough to rebuild its call.
def _retry_research(ctx: AppContext, runner: JobRunner, job) -> Job:
    subject = job.subject or ""
    if "@" in subject:
        symbol, day = subject.split("@", 1)
        again = submit_research(ctx, runner, symbol, date.fromisoformat(day), job.profile)
        _adopt_replay_cell(ctx, again, symbol, date.fromisoformat(day))
        return again
    return submit_research(ctx, runner, subject, profile=job.profile)


def _adopt_replay_cell(ctx: AppContext, job: Job, symbol: str, day: date) -> None:
    """A retried replay of a study cell that failed or waits for its retry counts for that
    cell, so the study never runs it a second time."""
    if job.profile is not None:
        return  # the study replays on the slots; a run on a picked profile is another experiment
    repo = ReplayRepo(ctx.conn)
    study = repo.current()
    cell = repo.cell_at(study.id, symbol, day) if study else None
    if cell and cell.state in (PENDING, FAILED):
        repo.adopt(cell.id, job.id, ctx.clock.utcnow())


def _retry_prices(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_prices(ctx, runner, None if job.subject == "*" else job.subject)


def _retry_analyse(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_analyse(ctx, runner, None if job.subject == "*" else job.subject)


def _retry_tick(ctx: AppContext, runner: JobRunner, job) -> Job:
    """Whatever started it before (scheduled or manual), a retry is asked for: a manual run of the same kind."""
    return submit_tick(ctx, runner, kind=MIDDAY if job.subject == MIDDAY else TICK)


def _retry_universe(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_universe_refresh(runner)


def _retry_merges(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_merges(runner)


def _retry_discover(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_discover(runner)


def _retry_ask(ctx: AppContext, runner: JobRunner, job) -> Job:
    return submit_rerun(ctx, runner, int(job.subject), job.profile)


def _retry_fx(ctx: AppContext, runner: JobRunner, job) -> Job:
    sources = [] if job.subject in (None, "*", "") else job.subject.split(",")
    return submit_fx(runner, sources)


def _retry_backtest(ctx: AppContext, runner: JobRunner, job) -> Job:
    # From the right: the other four parts never hold the separator, a list name might.
    name, start, end, slippage, commission = (job.subject or "").rsplit(BACKTEST_SEP, 4)
    return submit_backtest(ctx, runner, name, date.fromisoformat(start), date.fromisoformat(end), float(slippage),
                           float(commission))


_RETRY: dict[str, Callable[[AppContext, JobRunner, Any], Job]] = {
    "research": _retry_research, "prices": _retry_prices, "analyse": _retry_analyse, "tick": _retry_tick,
    "universe": _retry_universe, "merges": _retry_merges, "fx": _retry_fx, "discover": _retry_discover,
    "backtest": _retry_backtest, "ask": _retry_ask,
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
    running, and is a kind this app can rebuild on a profile that still exists. The newest
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
        out.append(row)
    return out


def retry_all(ctx: AppContext, runner: JobRunner) -> list[Job]:
    """Re-submit every job `retryable` finds, e.g. after a restart cancelled a batch at once."""
    return [_RETRY[row.kind](ctx, runner, row) for row in retryable(ctx, runner)]
