"""The numbers of a pre-registered predictive test: Spearman rank IC across companies per day,
Newey-West t-stats for overlapping return windows, per-feature signs and weights fitted on
trailing ICs, and the fixed pass bar. Numbers in, numbers out."""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from oracle.analysts.breaks import is_price, steady
from oracle.market.bars import Bar

__all__ = ["MIN_COUNTED", "MIN_DAYS", "MIN_NAMES", "PEER_SLACK_DAYS", "T_BAR", "Daily", "SegmentIC", "Verdict",
           "combine", "date_only", "fit_weights", "known_moment", "lookback", "mean", "newey_west_t", "own_return",
           "pct_ranks", "session_open", "span_return", "spearman", "trailing_return", "usable_day", "verdict"]

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


def verdict(ic: float | None, t: float | None, segments: Sequence[SegmentIC], bar: float = T_BAR,
            inclusive: bool = False) -> Verdict:
    """PASS only with the overall IC above 0, t above `bar` (or at it, `inclusive`) and the IC
    positive in more than half of the counted segments; fewer than MIN_COUNTED counted is
    inconclusive."""
    counted = [s for s in segments if s.counted]
    positive = sum(1 for s in counted if s.ic is not None and s.ic > 0)
    if len(counted) < MIN_COUNTED or ic is None:
        return Verdict("INCONCLUSIVE", False, ic, t, positive, len(counted))
    ok = ic > 0 and t is not None and (t >= bar if inclusive else t > bar) and positive * 2 > len(counted)
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


def trailing_return(bars: Sequence[Bar], days: Sequence[date], start: date, end: date) -> float | None:
    """Close of the last bar dated before `start` to close of the last dated before `end` (so
    known before `end`'s open), each within PEER_SLACK_DAYS; None with a split-sized move between."""
    i, j = bisect_left(days, start) - 1, bisect_left(days, end) - 1
    if i < 0 or j <= i or (start - days[i]).days > PEER_SLACK_DAYS or (end - days[j]).days > PEER_SLACK_DAYS:
        return None
    closes = [b.close for b in bars[i:j + 1]]
    if not all(is_price(x) for x in closes) or not steady(closes):
        return None
    return closes[-1] / closes[0] - 1


Daily = Mapping[date, Mapping[str, tuple[int, float, float]]]
"""First usable decision day -> relation -> (articles, sum of direction x prob, sum of relevance)."""


def lookback(daily: Daily, day: date, days: int, relations: Sequence[str]) -> dict[str, float]:
    """The features a decision on `day` sees: articles first usable on one of the `days` days up to
    and including it; `own_attention` = log(1 + own articles) is the news-attention baseline."""
    sums = {r: [0, 0.0, 0.0] for r in relations}
    for k in range(days):
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
            out["own_attention"] = math.log1p(n)
    return out


PACIFIC = ZoneInfo("America/Los_Angeles")


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).astimezone(timezone.utc)


def date_only(stamp: str) -> bool:
    """A stamp that gives a day, not a time: exactly midnight US Pacific (Google News) or UTC (SEC)."""
    t = _utc(stamp)
    return t.time() == time(0) or t.astimezone(PACIFIC).time() == time(0)


SEC_LATE = timedelta(hours=27)
"""EDGAR dates a filing made up to 22:00 ET with that day: 03:00 UTC the next day at the latest."""


def known_moment(stamp: str) -> datetime:
    """When an article was surely known, compared strictly with an open. A Google date-only stamp
    (midnight US Pacific) is known by the next Pacific midnight, so it is usable at an open at
    that moment: one microsecond before it. An SEC filing day (midnight UTC) is known at 03:00 UTC
    the next day. An exactly-timed stamp is known when stamped."""
    t = _utc(stamp)
    if t.time() == time(0):
        return t + SEC_LATE
    if date_only(stamp):
        day = t.astimezone(PACIFIC).date() + timedelta(days=1)
        return datetime.combine(day, time(0), PACIFIC).astimezone(timezone.utc) - timedelta(microseconds=1)
    return t


_OPENS = {
    "": ("America/New_York", time(9, 30)), "TO": ("America/Toronto", time(9, 30)),
    "SA": ("America/Sao_Paulo", time(10)), "L": ("Europe/London", time(8)),
    "DE": ("Europe/Berlin", time(9)), "F": ("Europe/Berlin", time(9)), "PA": ("Europe/Paris", time(9)),
    "AS": ("Europe/Amsterdam", time(9)), "MC": ("Europe/Madrid", time(9)), "MI": ("Europe/Rome", time(9)),
    "BR": ("Europe/Brussels", time(9)), "SW": ("Europe/Zurich", time(9)), "CO": ("Europe/Copenhagen", time(9)),
    "OL": ("Europe/Oslo", time(9)), "ST": ("Europe/Stockholm", time(9)), "HE": ("Europe/Helsinki", time(10)),
    "VI": ("Europe/Vienna", time(9)), "IR": ("Europe/Dublin", time(8)), "T": ("Asia/Tokyo", time(9)),
    "AX": ("Australia/Sydney", time(10)), "KS": ("Asia/Seoul", time(9)), "KQ": ("Asia/Seoul", time(9)),
    "SI": ("Asia/Singapore", time(9)), "HK": ("Asia/Hong_Kong", time(9, 20)), "NS": ("Asia/Kolkata", time(9, 8)),
    "BO": ("Asia/Kolkata", time(9, 8)), "SS": ("Asia/Shanghai", time(9, 25)), "SZ": ("Asia/Shanghai", time(9, 25)),
    "TW": ("Asia/Taipei", time(9)), "JO": ("Africa/Johannesburg", time(9)),
}
EARLIEST = ("Pacific/Auckland", time(10))
"""An exchange not listed opens at the earliest open there is, so nothing reaches it early."""


def _exchange(symbol: str) -> tuple[ZoneInfo, time]:
    suffix = symbol.rsplit(".", 1)[1].upper() if "." in symbol else ""
    zone, opens = _OPENS.get(suffix, EARLIEST)
    return ZoneInfo(zone), opens


def session_open(symbol: str, day: date) -> datetime:
    """The listing's open on its local `day`, in UTC."""
    zone, opens = _exchange(symbol)
    return datetime.combine(day, opens, zone).astimezone(timezone.utc)


def usable_day(symbol: str, known: datetime) -> date:
    """The first local day whose open comes strictly after `known`: the first decision it may feed."""
    zone, opens = _exchange(symbol)
    local = known.astimezone(zone)
    return local.date() + timedelta(days=1) if local.time() >= opens else local.date()
