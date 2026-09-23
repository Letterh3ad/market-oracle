from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool, StrictInt, StrictStr

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.schedule_service import schedule_status
from oracle.services.settings_service import set_peer_count, set_schedule

router = APIRouter(prefix="/api/settings")


class PeerCount(BaseModel):
    count: StrictInt


class Schedule(BaseModel):
    enabled: StrictBool
    time: StrictStr


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
