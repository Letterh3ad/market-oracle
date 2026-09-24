"""Exchange rates into the base currency: each currency in use, and one's history."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.fx_service import FX_SOURCES, credits, fx_history, fx_latest

router = APIRouter(prefix="/api/fx")


@router.get("")
async def latest(ctx: AppContext = Depends(get_ctx)) -> dict:
    rows = fx_latest(ctx)
    return {
        "base": ctx.config.base_currency,
        "sources": list(FX_SOURCES),
        "currencies": [{"currency": x.currency, "units_per_base": x.rate and x.rate.units_per_base,
                        "date": x.rate and x.rate.date, "source": x.rate and x.rate.source,
                        "age_days": x.age_days, "stale": x.stale} for x in rows],
        "credits": credits(x.rate.source for x in rows if x.rate),
    }


@router.get("/{currency}")
async def history(currency: str, days: int = Query(default=10, ge=1), ctx: AppContext = Depends(get_ctx)) -> dict:
    rates = fx_history(ctx, currency, days)
    return {"currency": currency.upper(), "base": ctx.config.base_currency,
            "rates": [asdict(r) for r in rates], "credits": credits(r.source for r in rates)}
