"""Price factors, point in time: 12-1 momentum (the return from a year ago to a month ago,
leaving out the last month, where short-term reversal lives) and 60-day realised volatility,
ranked as percentiles across a set of listings. Every function reads only bars on or before
the day it is given, so a backtest can call it on each rebalance day."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.analysts.factors import percentile
from oracle.market.bars import Bar

__all__ = ["LOOKBACK_DAYS", "PriceFactors", "Ranked", "factors_at", "rank", "ranked_at"]

YEAR = timedelta(days=365)
MONTH = timedelta(days=30)
VOL_RETURNS = 60
MAX_GAP = timedelta(days=10)
"""How far before its target day an anchor close may be: past this the listing was not
trading (or not fetched) then, and its return would be made up."""
LOOKBACK_DAYS = (YEAR + MAX_GAP).days
"""Calendar days of bars before the day that factors_at needs."""


@dataclass(frozen=True)
class PriceFactors:
    symbol: str
    as_of: date | None
    """The last close used, on or before the day."""
    momentum: float | None
    """12-1 return: 0.2 is up 20%."""
    volatility: float | None
    """Annualised standard deviation of the last 60 daily log returns."""
    note: str | None = None
    """Why a figure is missing."""


@dataclass(frozen=True)
class Ranked:
    factors: PriceFactors
    momentum_pct: float | None
    """Share of the ranked set its momentum beats: 1.0 is the strongest."""
    low_vol_pct: float | None
    """Share of the ranked set it is calmer than: 1.0 is the calmest."""


def factors_at(bars: Sequence[Bar], day: date) -> PriceFactors:
    """`bars` oldest first; any after `day` are ignored."""
    seen = [b for b in bars if b.date <= day]
    symbol = bars[0].symbol if bars else ""
    if not seen:
        return PriceFactors(symbol, None, None, None, "no prices")
    last = seen[-1].date
    if day - last > MAX_GAP:
        return PriceFactors(symbol, last, None, None, f"no price since {last.isoformat()}")
    window = [b for b in seen if b.date >= day - YEAR - MAX_GAP]
    close = _closes(window)
    momentum, notes = None, []
    start, end = _anchor(window, day - YEAR), _anchor(window, day - MONTH)
    if start is None or end is None:
        notes.append("less than a year of prices")
    else:
        momentum = close[end] / close[start] - 1
    recent = close[-(VOL_RETURNS + 1):]
    volatility = None
    if len(recent) <= VOL_RETURNS:
        notes.append(f"fewer than {VOL_RETURNS + 1} closes")
    else:
        returns = [math.log(b / a) for a, b in zip(recent, recent[1:])]
        volatility = statistics.stdev(returns) * math.sqrt(252)
    return PriceFactors(symbol, last, momentum, volatility, "; ".join(notes) or None)


def rank(factors: Mapping[str, PriceFactors]) -> dict[str, Ranked]:
    """Percentiles among the listings that have each figure; None with nothing to rank against."""
    def pct(value: float | None, pool: list[float], higher_is_better: bool) -> float | None:
        if value is None or len(pool) < 2:
            return None
        others = list(pool)
        others.remove(value)
        return percentile(value, others, higher_is_better)

    momenta = [f.momentum for f in factors.values() if f.momentum is not None]
    vols = [f.volatility for f in factors.values() if f.volatility is not None]
    return {s: Ranked(f, pct(f.momentum, momenta, True), pct(f.volatility, vols, False)) for s, f in factors.items()}


def ranked_at(history: Mapping[str, Sequence[Bar]], day: date) -> dict[str, Ranked]:
    """Each listing's factors on `day` and its percentiles across `history`'s listings."""
    return rank({s: factors_at(bars, day) for s, bars in history.items()})


def _anchor(window: Sequence[Bar], target: date) -> int | None:
    """Index of the last bar on or before `target`, if it is close enough to it."""
    i = next((k for k in range(len(window) - 1, -1, -1) if window[k].date <= target), None)
    return i if i is not None and target - window[i].date <= MAX_GAP else None


def _closes(bars: Sequence[Bar]) -> list[float]:
    # Adjusted closes carry splits and dividends. The ratio of two of them depends only on
    # what happened between the two days, so later adjustments do not leak into the past.
    adjusted = all(b.adj_close is not None and b.adj_close > 0 for b in bars)
    return [b.adj_close if adjusted else b.close for b in bars]
