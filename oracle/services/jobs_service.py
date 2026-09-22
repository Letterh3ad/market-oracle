from __future__ import annotations

from oracle.jobs import JobLock, JobStatus
from oracle.services.context import AppContext


def job_statuses(ctx: AppContext) -> list[JobStatus]:
    return JobLock(ctx.conn, ctx.clock).statuses()
