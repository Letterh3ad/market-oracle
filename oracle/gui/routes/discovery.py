from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.discovery_service import (
    discovered, move_discovered, remove_discovered, searched_sources,
)

router = APIRouter(prefix="/api/discovery")


@router.get("")
async def show(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The discovered list, each company with why it was found and the headlines behind it."""
    d = ctx.config.discovery
    return {
        "settings": {"enabled": d.enabled, "per_tick": d.per_tick, "max_total": d.max_total, "never": list(d.never),
                     "ai_trades": d.ai_trades, "themes": list(d.themes), "sources": searched_sources(ctx)},
        "items": [{
            "symbol": line.symbol, "name": line.name, "why": line.why, "source": line.source, "seed": line.seed,
            "relation": line.relation,
            "added_at": line.added_at,
            "evidence": [{**asdict(e), "link": shapes.web_link(e.url)} for e in line.evidence],
        } for line in discovered(ctx)],
    }


@router.delete("/{symbol}")
async def remove(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Off the discovered list for good: discovery never adds the company again."""
    return {"removed": remove_discovered(ctx, symbol)}


@router.post("/{symbol}/move")
async def move(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Onto the main watchlist and off the discovered one."""
    return asdict(move_discovered(ctx, symbol))
