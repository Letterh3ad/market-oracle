"""Everything the app is doing: jobs running, queued and recently finished."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import APIRouter, Depends, Query

from oracle.gui.deps import get_ctx, get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import JobRunner, retry, retry_all, retryable
from oracle.services.context import AppContext
from oracle.services.errors import UnknownJob
from oracle.services.jobs_service import held_elsewhere

router = APIRouter(prefix="/api/activity")


@router.get("")
async def activity(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """This server's running and queued jobs, plus work another process holds (a CLI run),
    which only it can stop. Finished jobs come from `/finished`."""
    return {
        "jobs": [shapes.job(j) for j in runner.current()],
        "elsewhere": [{"job": s.job.replace("_", " "), "pid": s.pid, "host": s.host, "age_s": s.age_s}
                      for s in held_elsewhere(ctx)],
    }


@router.get("/finished")
async def finished(page: int = Query(1, ge=1), q: str = "", runner: JobRunner = Depends(get_runner)) -> dict:
    """One page of finished jobs, filtered by `q` (label, kind, state or error)."""
    found = runner.finished_page(page, q)
    return {"items": [shapes.job(j) for j in found.items], "page": found.page, "pages": found.pages,
            "total": found.total}


@router.get("/finished/count")
async def finished_count(q: str = "", runner: JobRunner = Depends(get_runner)) -> dict:
    return {"total": runner.finished_count(q)}


@router.get("/retryable/count")
async def retryable_count(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """How many finished jobs `retry-all` would queue: cancelled, interrupted or failed,
    not since redone successfully, and not already queued or running."""
    return {"total": len(retryable(ctx, runner))}


@router.post("/retry-all", status_code=202)
async def retry_all_route(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    return {"jobs": [shapes.job(j) for j in retry_all(ctx, runner)]}


@router.get("/{job_id}")
async def get_job(job_id: int, runner: JobRunner = Depends(get_runner)) -> dict:
    job = runner.get(job_id)
    if job is None:
        raise UnknownJob(f"no job {job_id}")
    return shapes.job(job)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: int, runner: JobRunner = Depends(get_runner)) -> dict:
    """A queued job never starts; a running one stops (research records the run cancelled)."""
    job = runner.cancel(job_id)
    # Give the task a moment to unwind, so the answer usually shows its final state.
    with suppress(TimeoutError):
        await asyncio.wait_for(job.finished.wait(), 1.0)
    return shapes.job(job)


@router.post("/{job_id}/retry", status_code=202)
async def retry_job(job_id: int, ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Re-submit a finished job (cancelled, interrupted or failed) with the same subject."""
    return shapes.job(retry(ctx, runner, job_id))
