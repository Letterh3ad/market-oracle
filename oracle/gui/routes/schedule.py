"""The warning banner's buttons for the next scheduled tick. Stop on a running tick is
Activity's cancel."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_scheduler
from oracle.gui.routes import shapes
from oracle.services.schedule_service import Scheduler

router = APIRouter(prefix="/api/schedule")


@router.post("/postpone")
async def postpone(scheduler: Scheduler = Depends(get_scheduler)) -> dict:
    """An hour later, once per tick; a restart forgets it."""
    return shapes.upcoming(scheduler.postpone())


@router.post("/skip")
async def skip(scheduler: Scheduler = Depends(get_scheduler)) -> dict:
    return shapes.tick(scheduler.skip())
