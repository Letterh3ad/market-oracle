"""Total return: a listing's closes with its cash dividends reinvested, the one series every
held, index and benchmark line reads (boring-plan spec 1). Numbers in, numbers out."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

Closes = Sequence[tuple[date, float | None]]
Dividends = Sequence[tuple[date, float]]
"""(ex-date, cash per share), in the closes' currency and unit."""


def reinvest_factors(closes: Closes, dividends: Dividends) -> dict[date, float]:
    """Shares held per share bought at the first close, each dividend reinvested at its
    ex-date's close (the next close when the ex-date has none). A dividend on or before the
    first close was never held; one after the last close is left out."""
    pending = sorted((d, a) for d, a in dividends if a > 0)
    out: dict[date, float] = {}
    factor, i, first = 1.0, 0, None
    for day, close in closes:
        if first is None:
            first = day
            while i < len(pending) and pending[i][0] <= day:
                i += 1
        elif close is not None and close > 0:
            paid = 0.0
            while i < len(pending) and pending[i][0] <= day:
                paid += pending[i][1]
                i += 1
            factor *= 1 + paid / close
        out[day] = factor
    return out


def total_return(closes: Closes, dividends: Dividends) -> list[tuple[date, float | None]]:
    """Each close times its reinvestment factor: a series whose moves are the total return.
    A missing close stays missing."""
    factors = reinvest_factors(closes, dividends)
    return [(d, None if c is None else c * factors[d]) for d, c in closes]
