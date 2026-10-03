"""Walk-forward judging: the lines replayed over back-to-back out-of-sample segments, each a
fresh start on the same capital, so one lucky stretch cannot carry a line's whole record.
Numbers in, numbers out."""

from __future__ import annotations

import calendar
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from oracle.analysts.indicators import YEAR
from oracle.analysts.lab import Moments, moments
from oracle.analysts.risk import Series
from oracle.broker.compare import max_fall

MONTHS = 6


@dataclass(frozen=True)
class Figures:
    ret: float
    max_fall: float
    """Largest peak-to-trough fall, a negative ratio (0.0 with none)."""
    sharpe: float | None
    """Annualised mean over spread of the daily equity returns, a zero risk-free rate; None
    without a spread."""
    per_fall: float | None
    """Return over the largest fall's size; None without a fall."""
    excess: float | None = None
    """Return over the benchmark's in the same span."""
    moments: Moments | None = None
    """Over all segments only: the pooled daily returns' shape, for the deflated Sharpe."""


@dataclass(frozen=True)
class WalkLine:
    name: str
    segments: tuple[Figures, ...]
    overall: Figures
    """Segment returns compounded, the pooled daily returns' Sharpe, the worst segment's fall."""
    beat: int | None
    """Segments whose return was above the benchmark's; None for the benchmark itself."""


def segments(start: date, end: date, months: int = MONTHS) -> list[tuple[date, date]]:
    """Back-to-back spans of `months` from `start`, each counted from `start` so month ends
    never drift. A last span of half a segment or more is kept, shorter; a smaller one is too
    short to judge and is left out."""
    out = []
    k = 0
    while True:
        first = _add_months(start, k * months)
        if first > end:
            return out
        last = _add_months(start, (k + 1) * months) - timedelta(days=1)
        if last <= end:
            out.append((first, last))
        elif _add_months(first, months // 2) - timedelta(days=1) <= end:
            out.append((first, end))
            return out
        else:
            return out
        k += 1


def figures(equity: Series) -> Figures:
    if len(equity) < 2:
        return Figures(0.0, 0.0, None, None)
    ret = equity[-1][1] / equity[0][1] - 1
    fall = max_fall(equity)
    return Figures(ret, fall, _sharpe(_daily(equity)), _per_fall(ret, fall))


def judge(equities: Mapping[str, Sequence[Series]], benchmark: str) -> tuple[WalkLine, ...]:
    """Each line's figures per segment and over all of them, in the order given; every line has
    one equity series per segment, the benchmark's among them."""
    base = [figures(e) for e in equities[benchmark]]
    base_total = _compounded(f.ret for f in base)
    out = []
    for name, series in equities.items():
        each = [figures(e) for e in series]
        each = [replace(f, excess=f.ret - b.ret) for f, b in zip(each, base)]
        total = _compounded(f.ret for f in each)
        worst = min((f.max_fall for f in each), default=0.0)
        pooled = [r for e in series for r in _daily(e)]
        overall = Figures(total, worst, _sharpe(pooled), _per_fall(total, worst), total - base_total, moments(pooled))
        beat = None if name == benchmark else sum(1 for f in each if f.excess > 0)
        out.append(WalkLine(name, tuple(each), overall, beat))
    return tuple(out)


def _daily(equity: Series) -> list[float]:
    return [b / a - 1 for (_, a), (_, b) in zip(equity, equity[1:]) if a]


def _sharpe(daily: Sequence[float]) -> float | None:
    if len(daily) < 2:
        return None
    spread = statistics.stdev(daily)
    return statistics.fmean(daily) / spread * math.sqrt(YEAR) if spread > 0 else None


def _per_fall(ret: float, fall: float) -> float | None:
    return ret / -fall if fall < 0 else None


def _compounded(returns) -> float:
    return math.prod(1 + r for r in returns) - 1


def _add_months(day: date, months: int) -> date:
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
