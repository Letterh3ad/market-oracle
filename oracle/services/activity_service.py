"""Work started from the GUI, run as jobs: one queue per kind, so research runs one at a
time while a price update can run beside it, and every job is listed until the server
stops. Jobs are submitted from requests and run on the server's event loop."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from oracle.services.context import AppContext
from oracle.services.analysis_service import analyse_listing, analyse_watched
from oracle.services.errors import InvalidQuery, JobAlreadyRunning, ServiceError, UnknownJob
from oracle.services.research_service import research, research_progress
from oracle.services.routine_service import update_listing_prices, update_prices
from oracle.services.tick_service import record_skipped, run_tick
from oracle.services.universe_service import resolve_symbol
from oracle.store.job_history import JobHistoryRepo, JobRow

__all__ = ["Job", "JobRunner", "submit_analyse", "submit_prices", "submit_research", "submit_tick"]

log = logging.getLogger(__name__)

RECENT = 50
"""Finished jobs shown in the listing."""


@dataclass(eq=False)
class Job:
    id: int
    kind: str
    label: str
    work: Callable[[], Awaitable[Any]] | None = field(default=None, repr=False)
    """None for a job read back from history."""
    subject: str | None = None
    """What it works on (a symbol), so the same thing is never queued twice."""
    link: str | None = None
    """Where its result shows in the GUI."""
    watch: Callable[[], str | None] | None = field(default=None, repr=False)
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    state: str = "queued"
    """queued, running, done, failed or cancelled."""
    error: str | None = None
    result: Any = field(default=None, repr=False)
    finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    def progress(self) -> str | None:
        """What a running job is doing now, when its kind can tell."""
        return self.watch() if self.state == "running" and self.watch else None


class JobRunner:
    def __init__(self, ctx: AppContext) -> None:
        """Closes out what a previous server left queued or running: queued jobs never resume."""
        self._ctx = ctx
        self._history = JobHistoryRepo(ctx.conn)
        self._history.sweep(ctx.clock.utcnow())
        self._queues: dict[str, deque[Job]] = {}
        self._running: dict[str, Job] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._finished: deque[Job] = deque(maxlen=RECENT)

    def submit(self, kind: str, label: str, work: Callable[[], Awaitable[Any]], *, subject: str | None = None,
               link: str | None = None, progress: Callable[[], str | None] | None = None) -> Job:
        """Queue `work`; a job of the same kind and subject still waiting or running is
        returned instead of a second one."""
        if subject is not None:
            same = next((j for j in self.active(kind) if j.subject == subject), None)
            if same:
                return same
        now = self._ctx.clock.utcnow()
        job_id = self._history.add(kind, label, subject, link, "queued", now)
        job = Job(job_id, kind, label, work, subject, link, progress, submitted_at=now)
        self._queues.setdefault(kind, deque()).append(job)
        worker = self._workers.get(kind)
        if worker is None or worker.done():
            self._workers[kind] = asyncio.create_task(self._drain(kind))
        return job

    def jobs(self) -> list[Job]:
        """Running, then queued in order, then the last finished (earlier servers' too),
        newest first."""
        queued = [j for q in self._queues.values() for j in q]
        here = {j.id: j for j in self._finished}
        stored = [here.get(r.id) or _from_row(r) for r in self._history.finished(RECENT)]
        return [*self._running.values(), *sorted(queued, key=lambda j: j.id), *stored]

    def get(self, job_id: int) -> Job | None:
        live = [*self._running.values(), *(j for q in self._queues.values() for j in q), *self._finished]
        found = next((j for j in live if j.id == job_id), None)
        if found is None and (row := self._history.get(job_id)) is not None:
            found = _from_row(row)
        return found

    async def wait(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        await job.finished.wait()
        return job

    def cancel(self, job_id: int) -> Job:
        """Drop a queued job, or cancel a running one; the queue then moves on."""
        job = self.get(job_id)
        if job is None:
            raise UnknownJob(f"no job {job_id}")
        if job.state == "queued":
            self._queues[job.kind].remove(job)
            self._finish(job, "cancelled", "cancelled before it started")
        elif job.state == "running" and job.task is not None:
            job.task.cancel()
        else:
            raise InvalidQuery(f"{job.label} is already {job.state}")
        return job

    async def stop(self) -> None:
        """Cancel running work and drop what is queued; used when the server stops."""
        for queue in self._queues.values():
            while queue:
                self._finish(queue.popleft(), "cancelled", "not run: app closed")
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
            job.task = asyncio.create_task(job.work())
            try:
                job.result = await job.task
            except asyncio.CancelledError:
                self._finish(job, "cancelled", "cancelled")
                if asyncio.current_task().cancelling():
                    raise  # the server is stopping
            except Exception as exc:
                if not isinstance(exc, ServiceError):
                    log.exception("job %s failed", job.label)
                self._finish(job, "failed", str(exc) or type(exc).__name__)
            else:
                self._finish(job, "done", None)
            finally:
                self._running.pop(kind, None)

    def _finish(self, job: Job, state: str, error: str | None) -> None:
        job.state, job.error, job.finished_at = state, error, self._ctx.clock.utcnow()
        self._history.update(job.id, state, error, job.started_at, job.finished_at)
        self._finished.append(job)
        job.finished.set()


def _from_row(r: JobRow) -> Job:
    job = Job(r.id, r.kind, r.label, None, r.subject, r.link, None, r.submitted_at, r.started_at, r.finished_at,
              r.state, r.error)
    job.finished.set()
    return job


def submit_research(ctx: AppContext, runner: JobRunner, symbol: str) -> Job:
    """Queue one research run; an unknown symbol is refused now, not when its turn comes."""
    listing = resolve_symbol(ctx, symbol).symbol
    return runner.submit("research", f"Research {listing}", lambda: research(ctx, listing), subject=listing,
                         link=f"#company/{listing}", progress=lambda: research_progress(ctx, listing))


def submit_prices(ctx: AppContext, runner: JobRunner, symbol: str | None = None) -> Job:
    """Every watched listing, or one listing (watched or not)."""
    if symbol is None:
        return runner.submit("prices", "Update prices (watchlists)", lambda: update_prices(ctx), subject="*",
                             link="#watchlist")
    listing = resolve_symbol(ctx, symbol).symbol
    return runner.submit("prices", f"Update prices {listing}", lambda: update_listing_prices(ctx, listing),
                         subject=listing, link=f"#company/{listing}")


def submit_analyse(ctx: AppContext, runner: JobRunner, symbol: str | None = None) -> Job:
    if symbol is None:
        return runner.submit("analyse", "Analyse (watchlists)", lambda: analyse_watched(ctx), subject="*",
                             link="#watchlist")
    listing = resolve_symbol(ctx, symbol).symbol
    return runner.submit("analyse", f"Analyse {listing}", lambda: analyse_listing(ctx, listing), subject=listing,
                         link=f"#company/{listing}")


def submit_tick(ctx: AppContext, runner: JobRunner, trigger: str = "manual", skip_if_busy: bool = False) -> Job:
    """One tick as a job; its progress is the stage it is on. With `skip_if_busy` (scheduled
    ticks), a tick held by another process is recorded as skipped instead of failing."""
    stage: list[str] = []

    async def work():
        try:
            return await run_tick(ctx, trigger, on_stage=lambda name: stage.append(name))
        except JobAlreadyRunning as exc:
            if not skip_if_busy:
                raise
            return record_skipped(ctx, trigger, f"skipped: {exc}")

    return runner.submit("tick", f"Tick ({trigger})", work, subject="tick", link="#watchlist",
                         progress=lambda: stage[-1] if stage else None)
