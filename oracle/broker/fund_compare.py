"""Fund comparison math (boring-plan spec 2): a fund's yearly charge taken day by day, a yearly
rebalanced mix, and how far a real fund strayed from its modelled proxy. Numbers in, numbers out."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

Series = Sequence[tuple[date, float]]
YEAR = 365.25


def after_fees(series: Series, ter: float, embedded: float = 0.0) -> list[tuple[date, float]]:
    """The series with a yearly charge `ter` taken over calendar days from its first. `embedded`
    is a charge already inside the series (a proxy ETF's own fee), added back first."""
    if not series:
        return []
    first = series[0][0]
    keep = (1 - ter) / (1 - embedded)
    return [(d, v * keep ** ((d - first).days / YEAR)) for d, v in series]


def on_days(series: Series, days: Sequence[date]) -> list[tuple[date, float]]:
    """The series' last value on or before each day; days before its first are left out."""
    dates = [d for d, _ in series]
    out = []
    for day in days:
        k = bisect_right(dates, day)
        if k:
            out.append((day, series[k - 1][1]))
    return out


def blend(parts: Sequence[tuple[Series, float]]) -> list[tuple[date, float]]:
    """A mix of `parts` (series, weight) worth 1.0 on the first day they all have, rebalanced to
    the weights on each calendar year's first shared day. Only days every part has count."""
    if abs(sum(w for _, w in parts) - 1) > 1e-9:
        raise ValueError("a mix's weights must add to 1")
    maps = [dict(s) for s, _ in parts]
    shared = sorted(set.intersection(*(set(m) for m in maps)))
    if not shared:
        return []
    out: list[tuple[date, float]] = []
    units: list[float] = []
    for i, day in enumerate(shared):
        values = [m[day] for m in maps]
        if i == 0 or day.year != shared[i - 1].year:
            total = 1.0 if i == 0 else sum(u * v for u, v in zip(units, values))
            units = [total * w / v for (_, w), v in zip(parts, values)]
        out.append((day, sum(u * v for u, v in zip(units, values))))
    return out


def month_ends(series: Series) -> list[tuple[date, float]]:
    """The first point, each month's last and the last: enough for a chart over decades."""
    return [p for i, p in enumerate(series) if i == 0 or i == len(series) - 1
            or (series[i + 1][0].year, series[i + 1][0].month) != (p[0].year, p[0].month)]


@dataclass(frozen=True)
class Tracking:
    start: date
    end: date
    real_return: float
    model_return: float
    difference: float
    """Real minus modelled return over the overlap, as a ratio (0.01 is one point)."""
    yearly_difference: float
    """Real minus modelled yearly return (compounded over calendar days)."""


def tracking(real: Series, model: Series) -> Tracking | None:
    """The real fund against its modelled proxy from the real fund's first day to the last day
    both have, the proxy read on the real fund's days. None without an overlap of two days."""
    end = min(real[-1][0], model[-1][0]) if real and model else None
    days = [d for d, _ in real if end is not None and d <= end and d >= model[0][0]]
    if len(days) < 2:
        return None
    r, m = dict(real), dict(on_days(model, days))
    first, last = days[0], days[-1]
    years = (last - first).days / YEAR
    rr, mr = r[last] / r[first] - 1, m[last] / m[first] - 1
    return Tracking(first, last, rr, mr, rr - mr, (1 + rr) ** (1 / years) - (1 + mr) ** (1 / years))
