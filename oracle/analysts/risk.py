"""Per-listing risk in the base currency: a volatility-scaled position size, the worst
fall on record, and how closely it moves with the other watched listings. Portfolio risk
waits for the paper ledgers."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date

from oracle.analysts.indicators import MIN_BARS, YEAR, mean, volatility
from oracle.analysts.signals import AnalystSignal, Skipped

NAME = "risk"
# Shared trading days below this make a correlation noise.
MIN_SHARED_DAYS = 60

Series = Sequence[tuple[date, float]]
"""(date, close in the base currency), oldest first."""


def suggested_size(vol: float, *, account: float, max_position: float, cash_floor: float,
                   target_volatility: float) -> float:
    """At the target volatility a position gets the full cap; twice as volatile, half."""
    cap = account * max_position * min(1.0, target_volatility / vol) if vol > 0 else account * max_position
    return min(cap, account * (1 - cash_floor))


def max_drawdown(series: Series) -> tuple[float, date, date]:
    """(fall as a negative ratio, peak date, trough date)."""
    peak_value, peak_day = series[0][1], series[0][0]
    worst = (0.0, series[0][0], series[0][0])
    for day, value in series:
        if value > peak_value:
            peak_value, peak_day = value, day
        fall = value / peak_value - 1
        if fall < worst[0]:
            worst = (fall, peak_day, day)
    return worst


def correlation(a: Series, b: Series) -> float | None:
    """Of daily log returns, over the days both traded; None with too few in common."""
    other = dict(b)
    common = [(d, v, other[d]) for d, v in a if d in other]
    if len(common) < MIN_SHARED_DAYS:
        return None
    ra = [math.log(y[1] / x[1]) for x, y in zip(common, common[1:])]
    rb = [math.log(y[2] / x[2]) for x, y in zip(common, common[1:])]
    ma, mb = mean(ra), mean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    sa, sb = math.sqrt(sum((x - ma) ** 2 for x in ra)), math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (sa * sb) if sa and sb else None


def risk(symbol: str, series: Series, others: Mapping[str, Series], *, currency: str, account: float,
         max_position: float, cash_floor: float, target_volatility: float) -> AnalystSignal | Skipped:
    """`series` and `others` in the base currency, `currency`; `others` excludes `symbol`."""
    if len(series) < MIN_BARS:
        return Skipped(NAME, symbol, f"needs {MIN_BARS} days of prices in {currency}, has {len(series)}")
    closes = [v for _, v in series]
    vol = volatility(closes, window=min(YEAR, len(closes) - 1))[-1]
    size = suggested_size(vol, account=account, max_position=max_position, cash_floor=cash_floor,
                          target_volatility=target_volatility)
    fall, peak, trough = max_drawdown(series)
    corr = {s: c for s, other in others.items() if (c := correlation(series, other)) is not None}
    reason = (f"Suggested size {currency} {size:,.0f} ({size / account:.1%} of the {currency} {account:,.0f} paper "
              f"account): its swings "
              f"are {vol:.0%} a year against a {target_volatility:.0%} target. Worst fall on record: {fall:.0%} "
              f"from {peak.isoformat()} to {trough.isoformat()}.")
    if corr:
        top = max(corr, key=lambda s: abs(corr[s]))
        reason += f" Moves most in step with {top} ({corr[top]:+.2f}, where 1 is lockstep)."
    else:
        reason += " There is no other watched listing with enough shared history to compare against."
    figures = (("size", size), ("size_share", size / account), ("volatility", vol), ("max_drawdown", fall))
    figures += tuple((f"corr_{s}", c) for s, c in sorted(corr.items()))
    return AnalystSignal(NAME, symbol, series[-1][0], 0.0, 1.0, reason, figures)
