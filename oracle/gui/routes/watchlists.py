from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.watch_service import watch, watchlists

router = APIRouter(prefix="/api/watchlists")


class NewItem(BaseModel):
    symbol: str


@router.get("")
async def list_watchlists(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return [asdict(w) for w in watchlists(ctx)]


@router.post("/{name}/items")
async def add_item(name: str, body: NewItem, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(watch(ctx, body.symbol, name))
