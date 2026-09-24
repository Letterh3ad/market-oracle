from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool, StrictFloat, StrictInt, StrictStr

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.schedule_service import schedule_status
from oracle.services.settings_service import set_peer_count, set_schedule, set_setting, set_tick_warning, settings_form

router = APIRouter(prefix="/api/settings")


class Setting(BaseModel):
    section: StrictStr
    key: StrictStr
    value: StrictBool | StrictInt | StrictFloat | StrictStr | list[StrictStr] | None
    """None removes the key, so its default applies."""


class PeerCount(BaseModel):
    count: StrictInt


class Schedule(BaseModel):
    enabled: StrictBool
    time: StrictStr


class TickWarning(BaseModel):
    warn_minutes: StrictInt
    research_when_idle: StrictBool
    idle_minutes: StrictInt


@router.get("")
async def get_settings(ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"settings": settings_form(ctx)}


@router.put("")
async def put_setting(body: Setting, ctx: AppContext = Depends(get_ctx)) -> dict:
    return set_setting(ctx, body.section, body.key, body.value)


@router.put("/peers")
async def put_peers(body: PeerCount, ctx: AppContext = Depends(get_ctx)) -> dict:
    path = set_peer_count(ctx, body.count)
    return {"count": body.count, "path": str(path),
            "note": "saved; press Analyse to compare against the new peer group"}


@router.put("/schedule")
async def put_schedule(body: Schedule, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Takes effect on the running scheduler within a poll; no restart."""
    set_schedule(ctx, body.enabled, body.time)
    return shapes.schedule(schedule_status(ctx))


@router.put("/tick-warning")
async def put_tick_warning(body: TickWarning, ctx: AppContext = Depends(get_ctx)) -> dict:
    set_tick_warning(ctx, body.warn_minutes, body.research_when_idle, body.idle_minutes)
    return shapes.schedule(schedule_status(ctx))
