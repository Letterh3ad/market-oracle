from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.analysis_service import figure_meaning
from oracle.services.company_service import FIGURES, CompanyView, company_view
from oracle.services.context import AppContext, worker_context
from oracle.services.errors import InvalidQuery
from oracle.services.events_service import HORIZONS, earnings_study
from oracle.services.fx_service import credits
from oracle.services.edges_service import company_edges, confirm_edge, dismiss_edge
from oracle.services.industry_service import Activity, IndustryMap, company_activity, industry_map
from oracle.services.laya_service import MODEL_TAG, laya_scores
from oracle.services.links_service import USER_RELATIONS, CompanyLink, add_link, company_links, delete_link
from oracle.services.peers_service import choose_peers
from oracle.services.prices_service import price_history_in_base
from oracle.services.research_service import RunStatus, latest_research, report_history, research_run
from oracle.services.settings_service import PEERS_RANGE
from oracle.services.tags_service import add_tag, remove_tag

router = APIRouter(prefix="/api/companies")


class NewTag(BaseModel):
    tag: str


class NewLink(BaseModel):
    other: str
    relation: str


@router.get("/{symbol}")
async def get_company(symbol: str, refresh: bool = False, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Stored data only unless `refresh`, which fetches fundamentals older than today."""
    view = await company_view(ctx, symbol, refresh=refresh)
    return _view(view, ctx.config.base_currency)


@router.get("/{symbol}/prices")
async def get_prices(symbol: str, days: int = Query(default=365, ge=1, le=5000),
                     ctx: AppContext = Depends(get_ctx)) -> dict:
    """Closes in the listing's currency and the base one; `credits` covers every rate used."""
    history = price_history_in_base(ctx, symbol, days)
    return {
        "currency": history[-1].bar.currency if history else None,
        "base_currency": ctx.config.base_currency,
        "rows": [{"date": c.bar.date, "close": c.bar.close, "close_base": c.close_base} for c in history],
        "credits": credits(c.rate_source for c in history),
    }


@router.get("/{symbol}/research")
async def get_research(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The latest run with a report, with its trace, or `run: null` before any. A newer run
    that failed is named in `failed` so its error shows without hiding the last report."""
    latest = latest_research(ctx, symbol)
    reported = next(iter(report_history(ctx, symbol)), None)
    if reported is None or latest is None or latest.id == reported.id:
        return shapes.research_run(latest)
    return shapes.research_run(reported, latest if latest.status == RunStatus.FAILED else None)


@router.get("/{symbol}/research/history")
async def get_research_history(symbol: str, ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    """Every run with a report, newest first; open one by its id."""
    return [shapes.research_summary(r) for r in report_history(ctx, symbol)]


@router.get("/{symbol}/research/{run_id}")
async def get_research_run(symbol: str, run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    return shapes.research_run(research_run(ctx, symbol, run_id))


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


@router.get("/{symbol}/events")
async def get_events(symbol: str, refresh: bool = False, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Moves after past earnings from stored closes; `refresh` fetches release dates older than a week."""
    study = await earnings_study(ctx, symbol, refresh=refresh)
    return {**asdict(study), "headline": study.headline, "horizons": list(HORIZONS)}


@router.get("/{symbol}/links")
async def get_links(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Companies linked to this one in the news (or the same industry), strongest first."""
    return {"links": [_link(x) for x in company_links(ctx, symbol)]}


@router.get("/{symbol}/laya")
async def get_laya(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Laya's scores for the company's stored articles under the current model, newest known first;
    its probabilities are uncalibrated."""
    return {"model": MODEL_TAG, "enabled": ctx.config.laya.enabled, "uncalibrated": True,
            "scores": [{**asdict(s), "link": shapes.web_link(s.url)} for s in laya_scores(ctx, symbol)]}


@router.get("/{symbol}/edges")
async def get_edges(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Ties read from the company's headlines and 10-Ks, linked or waiting for the user, newest
    document first; Laya's probabilities are uncalibrated."""
    return {"enabled": ctx.config.laya.enabled, "uncalibrated": True,
            "edges": [{**asdict(e), "link": shapes.web_link(e.url)} for e in company_edges(ctx, symbol)]}


@router.post("/{symbol}/edges/{edge_id}/confirm")
async def confirm_tie(symbol: str, edge_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The user's yes: the tie becomes a link, first seen on its document's date."""
    return {"confirmed": asdict(confirm_edge(ctx, symbol, edge_id))}


@router.delete("/{symbol}/edges/{edge_id}")
async def delete_tie(symbol: str, edge_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The user's no: an unconfirmed tie is dropped."""
    return {"deleted": asdict(dismiss_edge(ctx, symbol, edge_id))}


@router.delete("/{symbol}/links/{link_id}")
async def remove_link(symbol: str, link_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every link between the two companies goes, and none is recorded again."""
    return {"deleted": _link(delete_link(ctx, symbol, link_id))}


@router.post("/{symbol}/links")
async def create_link(symbol: str, body: NewLink, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The user's own link: `other` is `relation` of the company."""
    return {"added": _link(add_link(ctx, symbol, body.other, body.relation))}


@router.get("/{symbol}/industry")
async def get_industry(symbol: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Related companies, how and since when, and what each is doing now; stored data only (about
    a second of reading, so on a thread and a connection of its own)."""
    def read() -> tuple[IndustryMap, dict[str, Activity]]:
        with worker_context(ctx) as own:
            m = industry_map(own, symbol)
            return m, company_activity(own, [r.symbol for r in m.related])

    m, now = await asyncio.to_thread(read)
    return {
        "symbol": m.symbol, "name": m.name, "industry": m.industry, "industry_group": m.industry_group,
        "basis": m.basis, "note": m.note, "relations": list(USER_RELATIONS),
        "related": [{
            "symbol": r.symbol, "name": r.name, "market_cap": r.market_cap, "link_id": r.link_id,
            "first_known": r.first_known, "relations": [asdict(x) for x in r.relations],
            "evidence": [shapes.headline(e) for e in r.evidence],
            "now": {**{k: v for k, v in asdict(now[r.symbol]).items() if k not in ("symbol", "headlines")},
                    "headlines": [shapes.headline(e) for e in now[r.symbol].headlines]},
        } for r in m.related],
    }


def _link(x: CompanyLink) -> dict:
    return {**asdict(x), "why": x.why, "evidence": [shapes.headline(e) for e in x.evidence]}


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
        "dead_since": v.dead_since,
        "siblings": [asdict(s) for s in v.siblings],
        "watchlists": list(v.watchlists),
        "fundamentals": None if f is None else {
            "source": f.source, "as_of": f.as_of,
            "figures": [{"name": fig.name, "label": fig.label, "kind": fig.kind, "meaning": fig.meaning,
                         "value": v.market_cap_base if fig.name == "market_cap" else getattr(f, fig.name)}
                        for fig in FIGURES],
        },
        "fundamentals_note": v.fundamentals_note,
        "signals": [{
            "analyst": s.signal.analyst, "direction": s.signal.direction.value, "score": s.signal.score,
            "confidence": s.signal.confidence, "reason": s.signal.reason,
            "figures": [{"name": n, "value": x, "meaning": figure_meaning(s.signal.analyst, n)}
                        for n, x in s.signal.figures],
            "as_of": s.signal.as_of, "stale": s.stale,
        } for s in v.signals],
        "lean": shapes.lean(lean, v.lean_stale),
        "next": shapes.next_step(price is not None, lean, v.lean_stale),
        "credits": credits([v.market_cap_rate_source, price.rate_source if price else None]),
    }
