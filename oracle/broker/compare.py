"""Equity lines side by side: returns since a start day, the largest fall, and an
equal-weight buy-and-hold to measure the accounts against. Numbers in, numbers out."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from oracle.analysts.risk import Series, max_drawdown

INDEX_SYMBOL, SPY_SYMBOL = "ACWI", "SPY"
INDEX_LINES = (("index", INDEX_SYMBOL, "Index (ACWI)"), ("spy", SPY_SYMBOL, "SPY"))
"""The broad-index buy-and-holds every table leads with (name, symbol, label): the honest "do
nothing" (next-signals spec 4). The index paper account holds the first."""


def returns_since(series: Series, start: date) -> list[tuple[date, float]]:
    """Each day's value against the first one on or after `start` (0.0 there)."""
    kept = [(d, v) for d, v in series if d >= start]
    return [(d, v / kept[0][1] - 1) for d, v in kept]


def max_fall(series: Series) -> float:
    """Largest peak-to-trough fall as a negative ratio; 0.0 with nothing to fall from."""
    return max_drawdown(series)[0] if series else 0.0


def benchmark_return(closes: Sequence[tuple[date, float | None]], start: date) -> list[tuple[date, float]]:
    """A single benchmark held from `start`, no costs: its close series from the first close
    on or after `start`, as returns. A day with no close (no rate, or a gap) is left out
    rather than carried forward, since nothing was bought with it. Empty with no close from
    `start` on."""
    usable = [(d, c) for d, c in closes if d >= start and c is not None]
    return returns_since(usable, start) if usable else []


def buy_and_hold(closes: Mapping[str, Sequence[tuple[date, float | None]]], start: date,
                 capital: float) -> tuple[list[tuple[date, float]], list[str]]:
    """`capital` split equally over the listings, each bought at its first close on or after
    `start` and held, no costs. A None close (no rate) or a missing day keeps the last
    value; a listing waits as cash until its first close. Listings with no close from
    `start` on are left out and returned by name."""
    usable = {s: [(d, c) for d, c in rows if d >= start and c is not None] for s, rows in closes.items()}
    left_out = sorted(s for s, rows in usable.items() if not rows)
    held = {s: rows for s, rows in usable.items() if rows}
    if not held:
        return [], left_out
    share = capital / len(held)
    units = {s: share / rows[0][1] for s, rows in held.items()}
    by_day: dict[date, dict[str, float]] = {}
    for s, rows in held.items():
        for d, c in rows:
            by_day.setdefault(d, {})[s] = c
    last: dict[str, float] = {}
    out = []
    for d in sorted(by_day):
        last.update(by_day[d])
        out.append((d, sum(units[s] * last[s] if s in last else share for s in held)))
    return out, left_out
