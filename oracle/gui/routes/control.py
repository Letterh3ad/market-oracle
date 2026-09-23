from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_control, get_runner
from oracle.services.activity_service import JobRunner
from oracle.services.app_service import AppControl

router = APIRouter(prefix="/api/app")


@router.post("/restart", status_code=202)
async def restart(control: AppControl = Depends(get_control), runner: JobRunner = Depends(get_runner)) -> dict:
    """Answers, then the server stops; work running or queued here ends with it."""
    cut_off = [j.label for kind in {j.kind for j in runner.jobs()} for j in runner.active(kind)]
    control.restart()
    return {"restarting": True, "cut_off": cut_off}
