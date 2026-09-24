from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.fx_service import credits
from oracle.services.watch_service import (
    WatchLine, create_watchlist, show_watchlist, swap, unwatch, watch, watchlists,
)

router = APIRouter(prefix="/api/watchlists")


class NewItem(BaseModel):
    symbol: str


class NewList(BaseModel):
    name: str


class Swap(BaseModel):
    old: str
    new: str


@router.get("")
async def list_watchlists(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return [asdict(w) for w in watchlists(ctx)]


@router.post("")
async def create(body: NewList, ctx: AppContext = Depends(get_ctx)) -> dict:
    name, created = create_watchlist(ctx, body.name)
    return {"name": name, "created": created}


@router.post("/swap")
async def swap_listing(body: Swap, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every list watching `old` watches `new` instead; paper positions stay on `old`."""
    return asdict(swap(ctx, body.old, body.new))


@router.get("/{name}")
async def show(name: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    lines = show_watchlist(ctx, name)
    return {
        "base_currency": ctx.config.base_currency,
        "items": [_item(line) for line in lines],
        "credits": credits(line.rate_source for line in lines),
    }


@router.post("/{name}/items")
async def add_item(name: str, body: NewItem, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(watch(ctx, body.symbol, name))


@router.delete("/{name}/items/{symbol}")
async def remove_item(name: str, symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"removed": unwatch(ctx, symbol, name)}


def _item(line: WatchLine) -> dict:
    return {
        "symbol": line.symbol, "name": line.name, "exchange": line.exchange, "country": line.country,
        "delisted": line.delisted, "missing": line.missing,
        "dead_since": line.dead_since, "siblings": [asdict(s) for s in line.siblings],
        "last_date": line.last_date, "last_close": line.last_close, "price_currency": line.price_currency,
        "close_base": line.close_base, "day_change": line.day_change, "stale": line.stale,
        "signals": [{"analyst": s.signal.analyst, "direction": s.signal.direction.value, "stale": s.stale}
                    for s in line.signals],
        "lean": shapes.lean(line.lean, line.lean_stale),
        "next": "remove it, or refresh the universe later" if line.missing
        else shapes.next_step(line.last_close is not None, line.lean, line.lean_stale),
    }
