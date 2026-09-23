from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictInt

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.settings_service import set_peer_count

router = APIRouter(prefix="/api/settings")


class PeerCount(BaseModel):
    count: StrictInt


@router.put("/peers")
async def put_peers(body: PeerCount, ctx: AppContext = Depends(get_ctx)) -> dict:
    path = set_peer_count(ctx, body.count)
    return {"count": body.count, "path": str(path),
            "note": "saved; press Analyse to compare against the new peer group"}
