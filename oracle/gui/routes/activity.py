"""Everything the app is doing: jobs running, queued and recently finished."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import JobRunner
from oracle.services.errors import UnknownJob

router = APIRouter(prefix="/api/activity")


@router.get("")
async def activity(runner: JobRunner = Depends(get_runner)) -> dict:
    return {"jobs": [shapes.job(j) for j in runner.jobs()]}


@router.get("/{job_id}")
async def get_job(job_id: int, runner: JobRunner = Depends(get_runner)) -> dict:
    job = runner.get(job_id)
    if job is None:
        raise UnknownJob(f"no job {job_id}: finished jobs are kept only while the app runs")
    return shapes.job(job)
