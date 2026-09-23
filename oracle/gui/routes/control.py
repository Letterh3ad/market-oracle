from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool

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


class AutostartBody(BaseModel):
    enabled: StrictBool


@router.get("/autostart")
async def get_autostart(control: AppControl = Depends(get_control)) -> dict:
    return _autostart(control)


@router.put("/autostart")
async def put_autostart(body: AutostartBody, control: AppControl = Depends(get_control)) -> dict:
    control.autostart.set(body.enabled)
    return _autostart(control)


def _autostart(control: AppControl) -> dict:
    path = control.autostart.path
    return {"enabled": control.autostart.enabled(), "supported": path is not None, "path": str(path) if path else None}
