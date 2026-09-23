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
from oracle.services.errors import InvalidQuery, ServiceError, UnknownJob
from oracle.services.research_service import research, research_progress
from oracle.services.universe_service import resolve_symbol

__all__ = ["Job", "JobRunner", "submit_research"]

log = logging.getLogger(__name__)

RECENT = 50
"""Finished jobs kept in the listing."""


@dataclass(eq=False)
class Job:
    id: int
    kind: str
    label: str
    work: Callable[[], Awaitable[Any]] = field(repr=False)
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
        self._ctx = ctx
        self._ids = 0
        self._queues: dict[str, deque[Job]] = {}
        self._running: dict[str, Job] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._finished: deque[Job] = deque(maxlen=RECENT)

    def submit(self, kind: str, label: str, work: Callable[[], Awaitable[Any]], *, subject: str | None = None,
               link: str | None = None, progress: Callable[[], str | None] | None = None) -> Job:
        """Queue `work`; a job of the same kind and subject still waiting or running is
        returned instead of a second one."""
        if subject is not None:
            same = next((j for j in self._active(kind) if j.subject == subject), None)
            if same:
                return same
        self._ids += 1
        job = Job(self._ids, kind, label, work, subject, link, progress, submitted_at=self._ctx.clock.utcnow())
        self._queues.setdefault(kind, deque()).append(job)
        worker = self._workers.get(kind)
        if worker is None or worker.done():
            self._workers[kind] = asyncio.create_task(self._drain(kind))
        return job

    def jobs(self) -> list[Job]:
        """Running, then queued in order, then finished newest first."""
        queued = [j for q in self._queues.values() for j in q]
        return [*self._running.values(), *sorted(queued, key=lambda j: j.id), *reversed(self._finished)]

    def get(self, job_id: int) -> Job | None:
        return next((j for j in self.jobs() if j.id == job_id), None)

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

    def _active(self, kind: str) -> list[Job]:
        running = self._running.get(kind)
        return ([running] if running else []) + list(self._queues.get(kind, ()))

    async def _drain(self, kind: str) -> None:
        queue = self._queues[kind]
        while queue:
            job = queue.popleft()
            job.state, job.started_at = "running", self._ctx.clock.utcnow()
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
        self._finished.append(job)
        job.finished.set()


def submit_research(ctx: AppContext, runner: JobRunner, symbol: str) -> Job:
    """Queue one research run; an unknown symbol is refused now, not when its turn comes."""
    listing = resolve_symbol(ctx, symbol).symbol
    return runner.submit("research", f"Research {listing}", lambda: research(ctx, listing), subject=listing,
                         link=f"#company/{listing}", progress=lambda: research_progress(ctx, listing))
