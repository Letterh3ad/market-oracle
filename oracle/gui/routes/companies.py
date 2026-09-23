from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.services.company_service import FIGURES, CompanyView, company_view
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.fx_service import credits
from oracle.services.peers_service import choose_peers
from oracle.services.prices_service import price_history_in_base
from oracle.services.settings_service import PEERS_RANGE
from oracle.services.tags_service import add_tag, remove_tag

router = APIRouter(prefix="/api/companies")


class NewTag(BaseModel):
    tag: str


@router.get("/{symbol}")
async def get_company(symbol: str, refresh: bool = False, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Stored data only unless `refresh`, which fetches fundamentals older than today."""
    view = await company_view(ctx, symbol, refresh=refresh)
    return _view(view, ctx.config.base_currency)


@router.get("/{symbol}/prices")
async def get_prices(symbol: str, days: int = Query(default=365, ge=1, le=5000),
                     ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    return [{"date": c.bar.date, "close": c.bar.close, "close_base": c.close_base}
            for c in price_history_in_base(ctx, symbol, days)]


@router.get("/{symbol}/peers")
async def get_peers(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Who the factor analysts compare against, from the universe alone (no fetching)."""
    shared = {"count": ctx.config.analysts.peers, "range": list(PEERS_RANGE)}
    try:
        choice = choose_peers(ctx, symbol)
    except InvalidQuery as exc:
        return {**shared, "basis": None, "fell_back": False, "peers": [], "note": str(exc)}
    return {**shared, "basis": choice.basis, "fell_back": choice.fell_back, "note": None,
            "peers": [{"symbol": r.symbol, "name": r.name, "market_cap": r.market_cap} for r in choice.rows]}


@router.post("/{symbol}/tags")
async def tag_company(symbol: str, body: NewTag, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(add_tag(ctx, symbol, body.tag))


@router.delete("/{symbol}/tags/{tag}")
async def untag_company(symbol: str, tag: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"removed": remove_tag(ctx, symbol, tag)}


def _view(v: CompanyView, base: str) -> dict:
    f, price, lean = v.fundamentals, v.price, v.lean
    return {
        "base_currency": base,
        "profile": asdict(v.profile),
        "price": None if price is None else {
            "date": price.bar.date, "close": price.bar.close, "currency": price.bar.currency,
            "close_base": price.close_base,
        },
        "day_change": v.day_change,
        "fundamentals": None if f is None else {
            "source": f.source, "as_of": f.as_of,
            "figures": [{"name": fig.name, "label": fig.label, "kind": fig.kind, "meaning": fig.meaning,
                         "value": v.market_cap_base if fig.name == "market_cap" else getattr(f, fig.name)}
                        for fig in FIGURES],
        },
        "fundamentals_note": v.fundamentals_note,
        "signals": [{
            "analyst": s.signal.analyst, "direction": s.signal.direction.value, "score": s.signal.score,
            "confidence": s.signal.confidence, "reason": s.signal.reason, "figures": s.signal.figures,
            "as_of": s.signal.as_of, "stale": s.stale,
        } for s in v.signals],
        "lean": None if lean is None else {
            "label": lean.label, "probability": lean.probability, "source": lean.source,
            "as_of": lean.as_of, "stale": v.lean_stale,
        },
        "next": "update prices" if price is None else "analyse" if lean is None or v.lean_stale else None,
        "credits": credits([v.market_cap_rate_source, price.rate_source if price else None]),
    }
