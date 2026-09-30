"""The numbers of a pre-registered predictive test: Spearman rank IC across companies per day,
Newey-West t-stats for overlapping return windows, per-feature signs and weights fitted on
trailing ICs, and the fixed pass bar. Numbers in, numbers out."""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.analysts.breaks import is_price, steady
from oracle.market.bars import Bar

__all__ = ["MIN_COUNTED", "MIN_DAYS", "MIN_NAMES", "PEER_SLACK_DAYS", "T_BAR", "Daily", "SegmentIC", "Verdict",
           "combine", "fit_weights", "lookback", "mean", "newey_west_t", "own_return", "pct_ranks", "span_return",
           "spearman", "verdict"]

MIN_NAMES = 5
"""Companies a day's IC needs."""
MIN_DAYS = 20
"""IC days a segment needs to count, and a feature's trailing fit to get a weight."""
MIN_COUNTED = 2
"""Counted segments a verdict needs; fewer is inconclusive."""
T_BAR = 2.0


def _ranks(xs: Sequence[float]) -> list[float]:
    """1-based ranks, ties given their average."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def pct_ranks(xs: Sequence[float]) -> list[float]:
    """Each value's rank scaled to 0..1 (ties averaged); a lone value is 0.5."""
    if len(xs) == 1:
        return [0.5]
    return [(r - 1) / (len(xs) - 1) for r in _ranks(xs)]


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sx * sy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation; None under MIN_NAMES pairs or with either side all equal."""
    if len(xs) < MIN_NAMES:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def newey_west_t(xs: Sequence[float], lags: int) -> float | None:
    """The mean over its Newey-West (Bartlett) standard error with `lags` lags; None without a
    spread."""
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    d = [x - m for x in xs]
    var = sum(v * v for v in d) / n
    for lag in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - lag / (lags + 1)) * sum(d[i] * d[i - lag] for i in range(lag, n)) / n
    if var <= 1e-18:
        return None
    return m / math.sqrt(var / n)


def fit_weights(train: Mapping[str, Sequence[float]], min_days: int = MIN_DAYS) -> dict[str, float]:
    """Each feature's weight: its mean trailing IC over the sum of their sizes, sign kept (0 for
    one with fewer than `min_days` days); all 0 when none has a mean IC."""
    means = {f: (sum(ics) / len(ics) if len(ics) >= min_days else 0.0) for f, ics in train.items()}
    total = sum(abs(v) for v in means.values())
    return {f: (v / total if total > 0 else 0.0) for f, v in means.items()}


def combine(features: Mapping[str, Sequence[float]], weights: Mapping[str, float]) -> list[float]:
    """Per company, the weighted sum of each feature's cross-sectional percentile rank."""
    n = len(next(iter(features.values())))
    out = [0.0] * n
    for f, xs in features.items():
        w = weights.get(f, 0.0)
        if w:
            out = [o + w * r for o, r in zip(out, pct_ranks(xs))]
    return out


@dataclass(frozen=True)
class SegmentIC:
    start: date
    end: date
    ic: float | None
    """Mean daily IC; None without an IC day."""
    t: float | None
    days: int
    counted: bool
    why: str | None = None
    """Why it is left out of the verdict."""


@dataclass(frozen=True)
class Verdict:
    label: str
    """PASS, FAIL or INCONCLUSIVE."""
    passed: bool
    ic: float | None
    t: float | None
    positive: int
    counted: int


def verdict(ic: float | None, t: float | None, segments: Sequence[SegmentIC]) -> Verdict:
    """PASS only with the overall IC above 0, t above T_BAR and the IC positive in more than half
    of the counted segments; fewer than MIN_COUNTED counted is inconclusive."""
    counted = [s for s in segments if s.counted]
    positive = sum(1 for s in counted if s.ic is not None and s.ic > 0)
    if len(counted) < MIN_COUNTED or ic is None:
        return Verdict("INCONCLUSIVE", False, ic, t, positive, len(counted))
    ok = ic > 0 and t is not None and t > T_BAR and positive * 2 > len(counted)
    return Verdict("PASS" if ok else "FAIL", ok, ic, t, positive, len(counted))


PEER_SLACK_DAYS = 5
"""How far a peer's first bar may be from the day asked, in calendar days."""


def _window(bars: Sequence[Bar], i: int, j: int) -> float | None:
    """Open of bar i to open of bar j; None with a missing open or a split-sized move among the
    closes and opens between (the close of the bar before i included)."""
    if not 0 <= i < j < len(bars):
        return None
    seq = ([bars[i - 1].close] if i > 0 else []) + [bars[i].open]
    seq += [b.close for b in bars[i:j]]
    seq.append(bars[j].open)
    if not all(is_price(x) for x in seq) or not steady(seq):
        return None
    return bars[j].open / bars[i].open - 1


def own_return(bars: Sequence[Bar], days: Sequence[date], day: date, h: int) -> tuple[float, date] | None:
    """The return from the open on `day` (a bar of its own) to the open `h` bars later, with that
    exit day; None without both bars."""
    i = bisect_left(days, day)
    if i >= len(days) or days[i] != day or i + h >= len(days):
        return None
    r = _window(bars, i, i + h)
    return None if r is None else (r, days[i + h])


def span_return(bars: Sequence[Bar], days: Sequence[date], start: date, end: date) -> float | None:
    """The return from the open of the first bar on or after `start` to the open of the first on
    or after `end`, each within PEER_SLACK_DAYS."""
    i, j = bisect_left(days, start), bisect_left(days, end)
    if j >= len(days) or (days[i] - start).days > PEER_SLACK_DAYS or (days[j] - end).days > PEER_SLACK_DAYS:
        return None
    return _window(bars, i, j)


Daily = Mapping[date, Mapping[str, tuple[int, float, float]]]
"""Feature day -> relation -> (articles, sum of direction x prob, sum of relevance)."""


def lookback(daily: Daily, day: date, days: int, relations: Sequence[str]) -> dict[str, float]:
    """The features a decision on `day` sees: feature days from `day - days` to the day before."""
    sums = {r: [0, 0.0, 0.0] for r in relations}
    for k in range(1, days + 1):
        for r, (n, score, relevance) in daily.get(day - timedelta(days=k), {}).items():
            if r in sums:
                s = sums[r]
                s[0] += n
                s[1] += score
                s[2] += relevance
    out: dict[str, float] = {}
    for r, (n, score, relevance) in sums.items():
        out[f"{r}_sum"] = score
        out[f"{r}_mean"] = score / n if n else 0.0
        if r == "own":
            out["own_relevance"] = relevance / n if n else 0.0
    return out
