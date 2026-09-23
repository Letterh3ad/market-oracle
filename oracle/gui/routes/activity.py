"""Everything the app is doing: jobs running, queued and recently finished."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx, get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import JobRunner
from oracle.services.context import AppContext
from oracle.services.errors import UnknownJob
from oracle.services.jobs_service import held_elsewhere

router = APIRouter(prefix="/api/activity")


@router.get("")
async def activity(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """This server's jobs, plus work another process holds (a CLI run), which only it can stop."""
    return {
        "jobs": [shapes.job(j) for j in runner.jobs()],
        "elsewhere": [{"job": s.job.replace("_", " "), "pid": s.pid, "host": s.host, "age_s": s.age_s}
                      for s in held_elsewhere(ctx)],
    }


@router.get("/{job_id}")
async def get_job(job_id: int, runner: JobRunner = Depends(get_runner)) -> dict:
    job = runner.get(job_id)
    if job is None:
        raise UnknownJob(f"no job {job_id}: finished jobs are kept only while the app runs")
    return shapes.job(job)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: int, runner: JobRunner = Depends(get_runner)) -> dict:
    """A queued job never starts; a running one stops (research records the run cancelled)."""
    job = runner.cancel(job_id)
    # Give the task a moment to unwind, so the answer usually shows its final state.
    with suppress(TimeoutError):
        await asyncio.wait_for(job.finished.wait(), 1.0)
    return shapes.job(job)
