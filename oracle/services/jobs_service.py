from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from oracle.jobs import JobBusy, JobLock, JobStatus, LockHandle
from oracle.services.context import AppContext
from oracle.services.errors import JobAlreadyRunning


def job_statuses(ctx: AppContext) -> list[JobStatus]:
    return JobLock(ctx.conn, ctx.clock).statuses()


def held_elsewhere(ctx: AppContext) -> list[JobStatus]:
    """Live locks another process holds (a CLI run, say): shown, never cancellable here."""
    return [s for s in job_statuses(ctx) if s.pid != os.getpid() and s.stale_reason is None]


class Held:
    def __init__(self, lock: JobLock, handle: LockHandle) -> None:
        self._lock = lock
        self._handle = handle

    def check(self) -> None:
        """Heartbeat; raise if another process has taken the job over."""
        if not self._lock.heartbeat(self._handle):
            raise JobAlreadyRunning(f"{self._handle.job} was taken over by another process; stopping")


@contextmanager
def single_flight(ctx: AppContext, job: str, lease_s: float) -> Iterator[Held]:
    """Run a job in one process at a time. Heartbeats are explicit (`check`) rather
    than a background task, so jobs stay deterministic under a fake clock; a crashed
    holder is freed by the lock's pid check, so the lease can be generous."""
    lock = JobLock(ctx.conn, ctx.clock)
    try:
        handle = lock.acquire(job, lease_s)
    except JobBusy as exc:
        raise JobAlreadyRunning(
            f"{exc.job.replace('_', ' ')} is already running (pid {exc.pid} on {exc.host}, "
            f"started {exc.age_s:.0f}s ago): wait for it to finish"
        ) from exc
    try:
        yield Held(lock, handle)
    finally:
        lock.release(handle)
