from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.fx_service import credits
from oracle.services.watch_service import WatchLine, show_watchlist, unwatch, watch, watchlists

router = APIRouter(prefix="/api/watchlists")


class NewItem(BaseModel):
    symbol: str


@router.get("")
async def list_watchlists(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return [asdict(w) for w in watchlists(ctx)]


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
    lean = line.lean
    return {
        "symbol": line.symbol, "name": line.name, "exchange": line.exchange, "country": line.country,
        "delisted": line.delisted, "missing": line.missing,
        "last_date": line.last_date, "last_close": line.last_close, "price_currency": line.price_currency,
        "close_base": line.close_base, "day_change": line.day_change, "stale": line.stale,
        "signals": [{"analyst": s.signal.analyst, "direction": s.signal.direction.value, "stale": s.stale}
                    for s in line.signals],
        "lean": None if lean is None else {
            "label": lean.label, "probability": lean.probability, "source": lean.source,
            "as_of": lean.as_of, "stale": line.lean_stale,
        },
        "next": _next(line),
    }


def _next(line: WatchLine) -> str | None:
    """What the row needs before it is complete, as the button that does it."""
    if line.missing:
        return "remove it, or refresh the universe later"
    if line.last_close is None:
        return "update prices"
    if line.lean is None or line.lean_stale:
        return "analyse"
    return None
