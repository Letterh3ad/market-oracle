"""The market's mood, one reading per day shared by every listing: an equal-weight composite
of the listings' daily returns, its trend and volatility, and three warning flags. Every
reading is a rolling one, so a day's mood reads only returns on or before it."""

from __future__ import annotations

import hashlib
import math
import statistics
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.analysts.breaks import is_price, steady
from oracle.analysts.factors import percentile
from oracle.analysts.indicators import YEAR

AVERAGE = 200
VOL_DAYS = 20
VOL_HIGH = 0.8
CORR_DAYS = 60
CORR_SAMPLE = 60
CORR_PRESENT = 0.9
"""Share of the window's days a listing must have a return on to join the correlation sample;
the rest count as flat, so one exchange's holidays do not drop it."""
CLUSTERED = 0.75
MIN_PAST = 20
"""Past readings a percentile needs before it means anything."""
MONTH = 21
REBOUND_MONTH = 0.08
CHOP_DAYS = 60
CHOP_CROSSES = 3


@dataclass(frozen=True)
class Mood:
    mood: str
    flags: frozenset[str]
    readings: Mapping[str, float]
    """trend_gap, vol_pct, ret_12m, ret_1m, and corr_pct when a sample was found."""


def step_returns(series: Sequence[tuple[date, float]]) -> dict[date, float]:
    """Day-over-day returns, a step outside the breaks guard (or a missing price) left out."""
    out = {}
    for (_, a), (day, b) in zip(series, series[1:]):
        if is_price(a) and is_price(b) and steady((a, b)):
            out[day] = b / a - 1
    return out


class Composite:
    """`returns` is each listing's step returns (base currency). Built once, read by day."""

    def __init__(self, returns: Mapping[str, Mapping[date, float]]) -> None:
        self.dates = sorted({d for r in returns.values() for d in r})
        at = {d: i for i, d in enumerate(self.dates)}
        sums, counts = [0.0] * len(self.dates), [0] * len(self.dates)
        aligned: dict[str, dict[int, float]] = {}
        for s, r in returns.items():
            aligned[s] = {at[d]: v for d, v in r.items()}
            for i, v in aligned[s].items():
                sums[i] += v
                counts[i] += 1
        self.returns = [a / n for a, n in zip(sums, counts)]
        self.level = list(_cumulative(self.returns))
        self._aligned = aligned
        self._order = sorted(aligned, key=lambda s: hashlib.sha1(s.encode()).hexdigest())
        self._present = {s: _prefix_counts(a, len(self.dates)) for s, a in aligned.items()}
        self.moods = self._moods()

    def index(self, day: date) -> int | None:
        i = bisect_right(self.dates, day) - 1
        return i if i >= 0 else None

    def return_on(self, day: date) -> float | None:
        i = self.index(day)
        return self.returns[i] if i is not None and self.dates[i] == day else None

    def mood(self, day: date) -> Mood | None:
        i = self.index(day)
        return None if i is None else self.moods[i]

    def _moods(self) -> list[Mood | None]:
        n = len(self.dates)
        average = rolling_mean(self.level, AVERAGE)
        vol = [None if i < VOL_DAYS else statistics.stdev(self.returns[i - VOL_DAYS + 1:i + 1]) * math.sqrt(YEAR)
               for i in range(n)]
        corr = [self._correlation(i) if i >= YEAR - 1 else None for i in range(n)]
        above = [None if a is None else lvl >= a for lvl, a in zip(self.level, average)]
        out: list[Mood | None] = []
        for i in range(n):
            if i < YEAR or vol[i] is None:
                out.append(None)
                continue
            vol_pct = _pct(vol[i], vol[i - YEAR:i])
            if vol_pct is None:
                out.append(None)
                continue
            readings = {"trend_gap": self.level[i] / average[i] - 1, "vol_pct": vol_pct,
                        "ret_12m": self.level[i] / self.level[i - YEAR] - 1,
                        "ret_1m": self.level[i] / self.level[i - MONTH] - 1}
            corr_pct = None if corr[i] is None else _pct(corr[i], corr[i - YEAR:i])
            if corr_pct is not None:
                readings["corr_pct"] = corr_pct
            flags = set()
            if corr_pct is not None and corr_pct >= CLUSTERED:
                flags.add("clustered")
            if readings["ret_12m"] < 0 and readings["ret_1m"] > REBOUND_MONTH:
                flags.add("rebound")
            crosses = sum(1 for k in range(i - CHOP_DAYS + 1, i + 1)
                          if above[k] is not None and above[k - 1] is not None and above[k] != above[k - 1])
            if crosses >= CHOP_CROSSES:
                flags.add("choppy")
            shape = "volatile" if vol_pct >= VOL_HIGH else "calm"
            out.append(Mood(f"{shape}_{'up' if above[i] else 'down'}", frozenset(flags), readings))
        return out

    def _correlation(self, i: int) -> float | None:
        """Mean pairwise correlation of a deterministic sample's returns over the window, from
        the variance of their standardised sum (exact, and linear in the sample's size)."""
        lo = i - CORR_DAYS + 1
        need = math.ceil(CORR_PRESENT * CORR_DAYS)
        sample = []
        for s in self._order:
            counts = self._present[s]
            if counts[i + 1] - counts[lo] < need:
                continue
            xs = [self._aligned[s].get(k, 0.0) for k in range(lo, i + 1)]
            mean = sum(xs) / CORR_DAYS
            sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / CORR_DAYS)
            if sd > 0:
                sample.append([(x - mean) / sd for x in xs])
                if len(sample) == CORR_SAMPLE:
                    break
        n = len(sample)
        if n < 2:
            return None
        total = sum(sum(z[k] for z in sample) ** 2 for k in range(CORR_DAYS))
        return (total / CORR_DAYS - n) / (n * (n - 1))


def _pct(value: float, past: Sequence[float | None]) -> float | None:
    others = [v for v in past if v is not None]
    return percentile(value, others, higher_is_better=True) if len(others) >= MIN_PAST else None


def _cumulative(returns: Sequence[float]):
    level = 1.0
    for r in returns:
        level *= 1 + r
        yield level


def rolling_mean(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            out[i] = total / window
    return out


def _prefix_counts(aligned: Mapping[int, float], n: int) -> list[int]:
    out, c = [0], 0
    for i in range(n):
        c += i in aligned
        out.append(c)
    return out
