"""The worst fall inside a crash window, how long the series took to get back to its peak, and what
a pot that kept paying in monthly went through (boring-plan 08's drawdown preview)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

__all__ = ["Fall", "Paying", "covers", "keep_paying", "months", "worst_fall"]

Series = Sequence[tuple[date, float]]

SLACK = timedelta(days=7)
"""A window opening on a holiday or weekend still counts as covered by a series starting days later."""


@dataclass(frozen=True)
class Fall:
    peak_day: date
    peak: float
    trough_day: date
    trough: float
    fall: float
    """trough / peak - 1, negative."""
    recovered: date | None
    """The first day after the trough at or above the peak, looked for to the series' end."""


@dataclass(frozen=True)
class Paying:
    """A pot bought at the peak that kept paying in at each month end after it."""

    value_at_trough: float
    paid_at_trough: float
    worst_gap: float
    """The most the pot was below the money paid in, negative."""
    worst_day: date
    even: date | None
    """The first day after the worst at or above the money paid in."""
    paid_at_even: float
    """Paid in by `even`, or by the series' end when it never got there."""


def covers(series: Series, start: date, end: date) -> bool:
    return bool(series) and series[0][0] <= start + SLACK and series[-1][0] >= end


def worst_fall(series: Series, start: date, end: date) -> Fall | None:
    """The deepest peak-to-trough fall with both ends inside [start, end]. None when the series does
    not span the window or never falls inside it."""
    if not covers(series, start, end):
        return None
    inside = [(d, v) for d, v in series if start <= d <= end]
    best: tuple[float, date, float, date, float] | None = None
    peak_day, peak = inside[0]
    for d, v in inside:
        if v >= peak:
            peak_day, peak = d, v
        fall = v / peak - 1
        if fall < 0 and (best is None or fall < best[0]):
            best = (fall, peak_day, peak, d, v)
    if best is None:
        return None
    fall, peak_day, peak, trough_day, trough = best
    recovered = next((d for d, v in series if d > trough_day and v >= peak), None)
    return Fall(peak_day, peak, trough_day, trough, fall, recovered)


def keep_paying(series: Series, fall: Fall, pot: float, monthly: float) -> Paying:
    """`pot` bought at the peak, then `monthly` at each month's last day in the series after it, until
    the pot is back to the money paid in (or the series ends)."""
    after = [(d, v) for d, v in series if d >= fall.peak_day]
    units, paid = pot / fall.peak, pot
    at_trough = (pot, pot)
    worst: tuple[float, date] | None = None
    for i, (d, price) in enumerate(after):
        if i and i + 1 < len(after) and after[i + 1][0].month != d.month:
            units += monthly / price
            paid += monthly
        value = units * price
        if d == fall.trough_day:
            at_trough = (value, paid)
        gap = value - paid
        if worst is None or gap < worst[0]:
            worst = (gap, d)
        elif d >= fall.trough_day and worst[0] < 0 and gap >= 0:
            return Paying(*at_trough, worst[0], worst[1], d, paid)
    return Paying(*at_trough, worst[0], worst[1], None, paid)


def months(a: date, b: date) -> int:
    """Whole months from `a` to `b`, a part month counted as one."""
    return (b.year - a.year) * 12 + b.month - a.month + (b.day > a.day)
