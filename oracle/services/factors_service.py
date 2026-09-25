"""Price factors for the user's view: each watched, held and discovered listing's 12-1
momentum and 60-day volatility, as percentiles across that whole set, from stored bars only.
Not read by research or the verdicts (DECISIONS 2026-09-25)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from oracle.analysts.price_factors import LOOKBACK_DAYS, Ranked, ranked_at
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.watch_service import watchlist_symbols
from oracle.store.bars import BarRepo
from oracle.store.paper import PaperRepo
from oracle.store.universe import UniverseRepo
from oracle.store.watchlists import WatchlistRepo

__all__ = ["FactorRow", "FactorTable", "company_factors", "factor_set", "factor_table"]


@dataclass(frozen=True)
class FactorRow:
    symbol: str
    name: str
    as_of: date | None
    """The last close used."""
    momentum: float | None
    momentum_pct: float | None
    """1.0 is the strongest in the set."""
    volatility: float | None
    low_vol_pct: float | None
    """1.0 is the calmest in the set."""
    note: str | None
    """Why a figure is missing."""
    in_set: bool = True
    """False for a company ranked against the set without being in it."""


@dataclass(frozen=True)
class FactorTable:
    as_of: date
    set_size: int
    """Watched, held and discovered listings."""
    ranked: int
    """Of them, those with a momentum rank."""
    watchlist: str | None
    """The list shown, or None for the whole set."""
    rows: tuple[FactorRow, ...]
    """Strongest momentum first; those too new to rank last."""


def factor_set(ctx: AppContext) -> list[str]:
    """Every watched (the discovered list included) and held listing, sorted."""
    return sorted(set(WatchlistRepo(ctx.conn).all_symbols()) | set(PaperRepo(ctx.conn).held_symbols()))


def factor_table(ctx: AppContext, as_of: date | None = None, watchlist: str | None = None) -> FactorTable:
    day = _day(ctx, as_of)
    symbols = factor_set(ctx)
    ranks = _ranks(ctx, symbols, day)
    shown = symbols
    if watchlist is not None:
        watchlist, shown = watchlist_symbols(ctx, watchlist)
    rows = sorted((_row(ctx, s, ranks[s]) for s in shown), key=_strongest_first)
    return FactorTable(day, len(symbols), sum(1 for r in ranks.values() if r.momentum_pct is not None),
                       watchlist, tuple(rows))


def company_factors(ctx: AppContext, symbol: str, as_of: date | None = None) -> FactorRow:
    """One listing ranked across the set, itself added to it when outside."""
    found = UniverseRepo(ctx.conn).resolve(symbol)
    symbol = found.symbol if found else symbol.strip()
    symbols = factor_set(ctx)
    in_set = symbol in symbols
    ranks = _ranks(ctx, symbols if in_set else [*symbols, symbol], _day(ctx, as_of))
    row = _row(ctx, symbol, ranks[symbol])
    return row if in_set else replace(row, in_set=False)


def _day(ctx: AppContext, as_of: date | None) -> date:
    # A day ahead of UTC is today somewhere east of it: clamped, not refused.
    today = ctx.clock.utcnow().date()
    if as_of is not None and as_of > today + timedelta(days=1):
        raise InvalidQuery(f"{as_of.isoformat()} is after today: factors read stored closes only")
    return min(as_of or today, today)


def _ranks(ctx: AppContext, symbols: list[str], day: date) -> dict[str, Ranked]:
    bars = BarRepo(ctx.conn)
    start = day - timedelta(days=LOOKBACK_DAYS)
    return ranked_at({s: bars.since(s, start, day) for s in symbols}, day)


def _row(ctx: AppContext, symbol: str, r: Ranked) -> FactorRow:
    found = UniverseRepo(ctx.conn).resolve(symbol)
    f = r.factors
    return FactorRow(symbol, found.name if found else "", f.as_of, f.momentum, r.momentum_pct, f.volatility,
                     r.low_vol_pct, f.note)


def _strongest_first(row: FactorRow) -> tuple:
    return (row.momentum_pct is None, -(row.momentum_pct or 0.0), row.symbol)
