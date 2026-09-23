from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool

from oracle.gui.deps import get_control, get_ctx, get_runner
from oracle.services.activity_service import JobRunner
from oracle.services.app_service import AppControl, install_shortcuts
from oracle.services.context import AppContext
from oracle.services.settings_service import set_automation

router = APIRouter(prefix="/api/app")


@router.post("/restart", status_code=202)
async def restart(control: AppControl = Depends(get_control), runner: JobRunner = Depends(get_runner)) -> dict:
    """Answers, then the server stops; work running or queued here ends with it."""
    cut_off = runner.active_labels()
    control.restart()
    return {"restarting": True, "cut_off": cut_off}


@router.post("/shutdown", status_code=202)
async def shut_down(control: AppControl = Depends(get_control), runner: JobRunner = Depends(get_runner)) -> dict:
    """Answers, then the process ends; the desktop shortcut starts it again."""
    cut_off = runner.active_labels()
    control.shut_down()
    return {"stopping": True, "cut_off": cut_off}


class AutomationBody(BaseModel):
    enabled: StrictBool
    stop_running: StrictBool = False
    """Turning off: also stop automatic work running or queued now."""


@router.put("/automation")
async def put_automation(body: AutomationBody, ctx: AppContext = Depends(get_ctx),
                         runner: JobRunner = Depends(get_runner)) -> dict:
    set_automation(ctx, body.enabled)
    stopped = runner.cancel_automatic() if not body.enabled and body.stop_running else []
    return {"enabled": ctx.config.automation.enabled, "stopped": [j.label for j in stopped]}


@router.post("/shortcuts")
async def post_shortcuts(control: AppControl = Depends(get_control)) -> dict:
    return {"written": [str(p) for p in install_shortcuts(control.command)]}


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
