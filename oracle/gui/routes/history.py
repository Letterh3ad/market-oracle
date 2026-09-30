"""What the history backfill has stored, per company and quarter, and the names each company's news
is searched by. The backfill itself is a job (`POST /api/jobs/history`)."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext, worker_context
from oracle.services.history_service import Coverage, clear_search_name, coverage, set_search_name
from oracle.services.watch_service import DEFAULT_LIST

router = APIRouter(prefix="/api/history")

CACHE_S = 60.0
"""The card reads every list company's map and a join over every backfilled article (about 20 s
on the live list after a full run): a view this recent is shown again."""
_cache: dict[str, tuple[float, dict]] = {}


class SearchNameBody(BaseModel):
    query: str = ""
    aliases: list[str] = []
    skip: bool = False


@router.get("/coverage")
async def history_coverage(watchlist: str = DEFAULT_LIST, fresh: bool = False,
                           ctx: AppContext = Depends(get_ctx)) -> dict:
    """On a thread and a connection of its own, and reused for CACHE_S unless `fresh`."""
    key = watchlist.casefold()
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]

    def read() -> Coverage:
        with worker_context(ctx) as own:
            return coverage(own, watchlist)

    body = shapes.coverage(await asyncio.to_thread(read))
    _cache[key] = (time.monotonic(), body)
    return body


@router.put("/names/{symbol}")
async def put_search_name(symbol: str, body: SearchNameBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Search the company's news by `query` (or with `skip` not at all); its news found so far is
    asked again under it."""
    primary, forgotten = set_search_name(ctx, symbol, body.query, body.aliases, body.skip)
    _cache.clear()
    return {"symbol": primary, "forgotten": forgotten}


@router.delete("/names/{symbol}")
async def delete_search_name(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    primary, forgotten = clear_search_name(ctx, symbol)
    _cache.clear()
    return {"symbol": primary, "forgotten": forgotten}
