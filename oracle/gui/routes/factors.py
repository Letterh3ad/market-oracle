from __future__ import annotations

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.factors_service import company_factors, factor_set, factor_table

router = APIRouter(prefix="/api/factors")


@router.get("")
async def get_factors(list: str | None = None, as_of: date | None = None,
                      ctx: AppContext = Depends(get_ctx)) -> dict:
    """12-1 momentum and 60-day volatility, ranked across the watched, held and discovered
    set; `list` shows one watchlist's rows. For the user's view only, never the verdicts."""
    return asdict(factor_table(ctx, as_of, list))


@router.get("/{symbol}")
async def get_company_factors(symbol: str, as_of: date | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {**asdict(company_factors(ctx, symbol, as_of)), "set_size": len(factor_set(ctx))}
