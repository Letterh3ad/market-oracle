"""The market's mood, one reading per day shared by every listing: an equal-weight composite
of the listings' daily returns, its trend and volatility, and three warning flags. Every
reading is a rolling one, so a day's mood reads only returns on or before it."""

from __future__ import annotations

import hashlib
import math
import statistics
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.analysts.breaks import is_price, steady
from oracle.analysts.factors import percentile
from oracle.analysts.indicators import YEAR, rolling_mean

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
THIN_SHARE = 0.5
ACTIVE_DAYS = 20


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
        every = sorted({d for r in returns.values() for d in r})
        at = {d: i for i, d in enumerate(every)}
        indexed = {s: sorted((at[d], v) for d, v in r.items()) for s, r in returns.items()}
        new = _full_days(indexed.values(), len(every))
        self.dates = [d for d, k in zip(every, new) if k is not None]
        n = len(self.dates)
        aligned = {s: _folded(r, new) for s, r in indexed.items()}
        sums, counts = [0.0] * n, [0] * n
        for a in aligned.values():
            for i, v in a.items():
                sums[i] += v
                counts[i] += 1
        self.returns = [a / c for a, c in zip(sums, counts)]
        self.level = list(_cumulative(self.returns))
        self.levels = {s: list(_cumulative([a.get(i, 0.0) for i in range(n)])) for s, a in aligned.items()}
        """symbol -> its own cumulative level on the composite's days, flat where it has no return."""
        self._aligned = aligned
        self._order = sorted(aligned, key=lambda s: hashlib.sha1(s.encode()).hexdigest())
        self._present = {s: _prefix_counts(a, n) for s, a in aligned.items()}
        self.moods = self._moods()

    def excess(self, symbols: Sequence[str], k: int, days: int) -> float:
        """The listings' mean return from the composite's day k to day k + days, over the composite's."""
        mean = sum(self.levels[s][k + days] / self.levels[s][k] for s in symbols) / len(symbols)
        return mean - self.level[k + days] / self.level[k]

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


def _full_days(returns: Iterable[Sequence[tuple[int, float]]], n: int) -> list[int | None]:
    """Each day's index among the full days, None for a thin one: a day is full when at
    least THIN_SHARE of the listings with a return in the ACTIVE_DAYS before it have one, so
    a holiday bar or a partial session never reads as the whole market."""
    counts, starts = [0] * n, [0] * (n + ACTIVE_DAYS + 2)
    for r in returns:
        end = -1
        for i, _ in r:
            counts[i] += 1
            lo, hi = max(i + 1, end + 1), i + ACTIVE_DAYS
            if lo <= hi:
                starts[lo] += 1
                starts[hi + 1] -= 1
                end = hi
    out: list[int | None] = []
    active, k = 0, 0
    for i in range(n):
        active += starts[i]
        if counts[i] and counts[i] >= THIN_SHARE * active:
            out.append(k)
            k += 1
        else:
            out.append(None)
    return out


def _folded(returns: Sequence[tuple[int, float]], new: Sequence[int | None]) -> dict[int, float]:
    """A listing's returns on the full days, each carrying its moves from the thin days before it."""
    out, carried = {}, 1.0
    for i, v in returns:
        carried *= 1 + v
        if new[i] is not None:
            out[new[i]] = carried - 1
            carried = 1.0
    return out


def _prefix_counts(aligned: Mapping[int, float], n: int) -> list[int]:
    out, c = [0], 0
    for i in range(n):
        c += i in aligned
        out.append(c)
    return out
