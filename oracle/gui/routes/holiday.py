"""The App section's Holiday watch card. Arming lists processes, reads power settings, backs the
database up and runs schtasks: a thread and a connection of its own keep the page answering."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx, get_holiday
from oracle.services.context import AppContext, worker_context
from oracle.services.holiday_service import Holiday

router = APIRouter(prefix="/api/holiday")


async def _run(ctx: AppContext, holiday: Holiday, command: str) -> dict:
    def work() -> dict:
        with worker_context(ctx) as own:
            return getattr(holiday, command)(own).to_json()

    return await asyncio.to_thread(work)


@router.get("")
async def status(ctx: AppContext = Depends(get_ctx), holiday: Holiday = Depends(get_holiday)) -> dict:
    """The checks without their side effects, the tasks, the study and the watch's status."""
    return await _run(ctx, holiday, "status")


@router.post("/arm")
async def arm(ctx: AppContext = Depends(get_ctx), holiday: Holiday = Depends(get_holiday)) -> dict:
    """Refuses (ok false) on any failed check; nothing is started then."""
    return await _run(ctx, holiday, "arm")


@router.post("/disarm")
async def disarm(ctx: AppContext = Depends(get_ctx), holiday: Holiday = Depends(get_holiday)) -> dict:
    return await _run(ctx, holiday, "disarm")
