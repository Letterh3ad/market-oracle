from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.universe_service import FILTER_FIELDS, SORTS, facets, search_universe, universe_status

router = APIRouter(prefix="/api/universe")


@router.get("/search")
async def search(
    request: Request,
    q: str = "",
    sort: str | None = None,
    desc: bool = False,
    tag: list[str] = Query(default=[]),
    all_listings: bool = False,
    include_delisted: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: AppContext = Depends(get_ctx),
) -> dict:
    """Filters come as repeatable query parameters named after the field (`country=Japan`)."""
    filters = {name: request.query_params.getlist(name) for name in FILTER_FIELDS}
    # One extra row answers "is there another page" without a count query.
    hits = search_universe(ctx, q, filters=filters, tags=tag, sort=sort, descending=desc,
                           all_listings=all_listings, include_delisted=include_delisted,
                           limit=limit + 1, offset=offset)
    return {"hits": [asdict(h) for h in hits[:limit]], "more": len(hits) > limit}


@router.get("/status")
async def status(ctx: AppContext = Depends(get_ctx)) -> dict:
    """What is loaded, by type, and when it was refreshed (over half a second on the full universe)."""
    return asdict(universe_status(ctx))


@router.get("/options")
async def options() -> dict:
    return {"filters": list(FILTER_FIELDS), "sorts": list(SORTS)}


@router.get("/facets/{field}")
async def get_facets(field: str, ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return [asdict(f) for f in facets(ctx, field)]
