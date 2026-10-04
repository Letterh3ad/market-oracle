"""The numbers of the general pre-registered signal test (next-signals 02): a daily price panel
on the trading calendar, sector-excess open-to-open returns, the daily Spearman IC, each window's
IC, Newey-West t, counted segments and verdict, and its power. The IC, t and verdict are
predictive.py's, shared with Laya's test. Numbers in, numbers out."""

from __future__ import annotations

import math
from array import array
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.analysts.breaks import SPLIT_RATIOS
from oracle.analysts.predictive import (
    MIN_DAYS, MIN_NAMES, T_BAR, SegmentIC, mean, newey_west_t, spearman, verdict,
)
from oracle.market.bars import Bar

__all__ = ["INSIDE", "unseen_text", "MIN_SECTOR", "Book", "Panel", "SegmentRow", "Signal", "WindowResult", "break_even",
           "book_trading", "daily_ics", "excess", "top_books",
           "overall", "power", "reversal_1m", "momentum_12_1", "window_result"]

MIN_SECTOR = 3
"""Other same-sector members with a return a benchmark needs; else the other universe members'."""
INSIDE = "inside the model's training"
NAN = float("nan")


def _price(x: float) -> bool:
    return x > 0  # NaN compares False


def _steady(a: float, b: float) -> bool:
    low, high = SPLIT_RATIOS
    return low <= b / a <= high


class Panel:
    """Adjusted daily opens and closes of every ticker on the trading calendar (NaN without a
    bar), each day's members, and sectors. A score reads index `i - 1` and before only."""

    def __init__(self, days: Sequence[date], members: Sequence[frozenset[str]], sectors: Mapping[str, str | None]):
        self.days = list(days)
        self.at = {d: i for i, d in enumerate(self.days)}
        self.members = list(members)
        self.sectors = dict(sectors)
        self.open: dict[str, array] = {}
        self.close: dict[str, array] = {}
        self.split_close: dict[str, array] = {}
        """Yahoo's close as stored: split-adjusted to today's shares, dividends not taken out."""
        self._bad: dict[str, array] = {}
        self.extra: dict[str, object] = {}
        """What a signal's `prepare` loaded for its score (fundamentals, say)."""

    def add(self, symbol: str, bars: Sequence[Bar]) -> None:
        """`symbol`'s bars on the calendar, adjusted by each bar's adj_close / close (one fetch's basis)."""
        n = len(self.days)
        o, c, sc = array("d", [NAN]) * n, array("d", [NAN]) * n, array("d", [NAN]) * n
        for b in bars:
            i = self.at.get(b.date)
            if i is None or not (b.close and b.close > 0):
                continue
            k = b.adj_close / b.close if b.adj_close and b.adj_close > 0 else 1.0
            sc[i] = b.close
            c[i] = b.close * k
            o[i] = b.open * k if b.open and b.open > 0 else NAN
        # bad[i]: closes 0..i hold that many missing closes or split-sized steps into them.
        bad, run = array("i", [0]) * n, 0
        for i in range(n):
            if not _price(c[i]) or (i and _price(c[i - 1]) and not _steady(c[i - 1], c[i])):
                run += 1
            bad[i] = run
        self.open[symbol], self.close[symbol], self.split_close[symbol], self._bad[symbol] = o, c, sc, bad

    def _clean(self, s: str, a: int, b: int) -> bool:
        """Closes a..b all prices with no split-sized step between."""
        c, bad = self.close[s], self._bad[s]
        return 0 <= a <= b < len(c) and _price(c[a]) and bad[b] == bad[a]

    def trailing(self, s: str, a: int, b: int) -> float | None:
        """Close of day a to close of day b, None with a gap or a split-sized move between."""
        if s not in self.close or not self._clean(s, a, b):
            return None
        c = self.close[s]
        return c[b] / c[a] - 1

    def forward(self, s: str, i: int, h: int) -> float | None:
        """Open of day i to open of day i + h, None without both opens, with a gap in the closes
        between (the close before i included) or a split-sized move among them."""
        j = i + h
        o, c = self.open.get(s), self.close.get(s)
        if o is None or i < 1 or j >= len(o) or not (_price(o[i]) and _price(o[j])):
            return None
        if not self._clean(s, i - 1, j - 1) or not _steady(c[i - 1], o[i]) or not _steady(c[j - 1], o[j]):
            return None
        return o[j] / o[i] - 1

    def priced(self, s: str, i: int) -> bool:
        c = self.close.get(s)
        return c is not None and _price(c[i])


Score = Callable[[Panel, int, Sequence[str]], Mapping[str, float]]
"""(panel, decision day index, the day's members) -> score per member it can score; it may read
only days before the decision day (decisions are made at its open)."""


@dataclass(frozen=True)
class Signal:
    name: str
    label: str
    horizons: tuple[int, ...]
    """Trading days from the decision day's open to the exit open."""
    score: Score
    model: str | None = None
    """The model tag of a model-read signal: it counts only after that model's training cutoff."""
    prepare: Callable[[object, Panel], None] | None = None
    """(research context, panel): loads what `score` reads into `panel.extra`, once per run."""
    keep_priced_duplicate: bool = False
    """Of a known duplicate pair on the same day, keep the listing with a bar that day (gate 02 L3);
    False keeps the earlier listing, as the first two signals were pre-registered."""


MOMENTUM_SKIP, MOMENTUM_YEAR, REVERSAL_DAYS = 21, 252, 21


def momentum_12_1(p: Panel, i: int, names: Sequence[str]) -> dict[str, float]:
    """The return from the close 252 trading days before the last known close to the close 21
    before it: the past year leaving out the last month."""
    a, b = i - 1 - MOMENTUM_YEAR, i - 1 - MOMENTUM_SKIP
    return {s: r for s in names if (r := p.trailing(s, a, b)) is not None}


def reversal_1m(p: Panel, i: int, names: Sequence[str]) -> dict[str, float]:
    """Minus the last 21 trading days' return to the last known close: last month's losers score high."""
    a, b = i - 1 - REVERSAL_DAYS, i - 1
    return {s: -r for s in names if (r := p.trailing(s, a, b)) is not None}


def excess(returns: Mapping[str, float], sectors: Mapping[str, str | None]) -> dict[str, float]:
    """Each return minus the equal-weight mean of the other same-sector names' (MIN_SECTOR or more),
    else of every other name's (MIN_SECTOR or more); a name with neither is left out."""
    total, n = sum(returns.values()), len(returns)
    by: dict[str | None, list[float]] = {}
    for s, r in returns.items():
        by.setdefault(sectors.get(s), []).append(r)
    sums = {k: (sum(v), len(v)) for k, v in by.items()}
    out = {}
    for s, r in returns.items():
        sec = sectors.get(s)
        if sec is not None and sums[sec][1] - 1 >= MIN_SECTOR:
            out[s] = r - (sums[sec][0] - r) / (sums[sec][1] - 1)
        elif n - 1 >= MIN_SECTOR:
            out[s] = r - (total - r) / (n - 1)
    return out


DayIC = tuple[float, int, date]
"""(IC, names, exit day)."""


def daily_ics(p: Panel, score: Score, h: int, first: int = 1, last: int | None = None) -> dict[date, DayIC]:
    """Per decision day index from `first` to before `last`: the Spearman IC of the score against
    the h-day sector-excess return over that day's members (MIN_NAMES or more)."""
    out: dict[date, DayIC] = {}
    for i in range(max(first, 1), min(len(p.days) - h, len(p.days) if last is None else last)):
        names = sorted(p.members[i])
        rets = {s: r for s in names if (r := p.forward(s, i, h)) is not None}
        if len(rets) < MIN_NAMES:
            continue
        ex = excess(rets, p.sectors)
        scores = score(p, i, names)
        both = [s for s in names if s in ex and s in scores]
        ic = spearman([scores[s] for s in both], [ex[s] for s in both])
        if ic is not None:
            out[p.days[i]] = (ic, len(both), p.days[i + h])
    return out


TOP_SHARE = 0.2
Book = tuple[float, frozenset[str], date]
"""(the top fifth's mean h-day sector-excess return, its names, exit day)."""


def top_books(p: Panel, score: Score, h: int, first: int = 1, last: int | None = None) -> dict[date, Book]:
    """The equal-weight book of the top fifth by score (at least one name), held h days, on every
    h-th decision day index (1, 1 + h, ...) from `first` to before `last`, over the same names as
    that day's IC."""
    out: dict[date, Book] = {}
    for i in range(max(first, 1), min(len(p.days) - h, len(p.days) if last is None else last)):
        if (i - 1) % h:
            continue
        names = sorted(p.members[i])
        rets = {s: r for s in names if (r := p.forward(s, i, h)) is not None}
        if len(rets) < MIN_NAMES:
            continue
        ex = excess(rets, p.sectors)
        scores = score(p, i, names)
        both = [s for s in names if s in ex and s in scores]
        if len(both) < MIN_NAMES:
            continue
        top = sorted(both, key=lambda s: (-scores[s], s))[:max(1, int(len(both) * TOP_SHARE))]
        out[p.days[i]] = (sum(ex[s] for s in top) / len(top), frozenset(top), p.days[i + h])
    return out


def _turnover(a: frozenset[str], b: frozenset[str]) -> float:
    """One-way: the share of an equal-weight book `a` sold to hold `b`."""
    return sum(abs((s in a) / len(a) - (s in b) / len(b)) for s in a | b) / 2


def break_even(gross: Sequence[float], turnover: Sequence[float]) -> float | None:
    """The one-way cost per trade, as a share of the value traded, that takes the mean gross edge
    per book to zero: each book sells and buys its turnover. None without a gross edge."""
    g, t = mean(gross), mean(turnover)
    return g / (2 * t) if g is not None and t and g > 0 else None


def book_trading(books: Mapping[date, Book], start: date, end: date) -> tuple[float | None, float | None]:
    """The books decided from `start` with their exit by `end`: mean one-way turnover per book (each
    from the book before it, inside the window or not) and the break-even cost."""
    ordered = sorted(books.items())
    gross, turns = [], []
    for k, (d, (g, names, exit_day)) in enumerate(ordered):
        if start <= d and exit_day <= end:
            gross.append(g)
            if k:
                turns.append(_turnover(ordered[k - 1][1][1], names))
    return _r(mean(turns)), _r(break_even(gross, turns), 6)


def power(names: float, days: int, h: int, bar: float = T_BAR) -> float | None:
    """The smallest mean IC whose t reaches `bar`, with `names` per day over `days` IC days: a
    day's IC spreads 1/sqrt(names - 1) by chance, and h-day windows overlap, so `days` / h of them
    are independent (checked against a simulation of a held-still score with a known IC)."""
    if names <= 1 or days <= 0:
        return None
    return bar * math.sqrt(h / ((names - 1) * days))


@dataclass(frozen=True)
class SegmentRow:
    start: date
    end: date
    ic: float | None
    t: float | None
    days: int
    counted: bool
    why: str | None = None


@dataclass(frozen=True)
class WindowResult:
    window: str
    """"train" (chooses and reports only) or "confirm" (the verdict)."""
    start: date
    end: date
    horizon: int
    ic: float | None
    t: float | None
    days: int
    """IC days in the counted segments."""
    names: float | None
    """Mean names per counted IC day."""
    effective_days: float
    power: float | None
    """Smallest IC detectable at the bar t from names, effective days and horizon."""
    observed_power: float | None
    """The bar t times the counted ICs' own Newey-West standard error."""
    missing_share: float | None
    """Member-days in the window without a bar."""
    segments: tuple[SegmentRow, ...]
    verdict: str
    passed: bool
    positive: int
    counted: int
    turnover: float | None = None
    """Mean one-way turnover per h-day rebalance of the top-fifth book; None in results kept before it."""
    break_even: float | None = None
    """The one-way cost per trade (share of value) that takes that book's sector-excess edge to zero."""

    @property
    def unseen(self) -> bool:
        """A FAIL whose IC is above 0 but under what the window's own spread lets it see at the bar t."""
        return (self.verdict == "FAIL" and self.ic is not None and self.observed_power is not None
                and 0 < self.ic < self.observed_power)

    @property
    def verdict_text(self) -> str:
        return unseen_text(self.observed_power) if self.unseen else self.verdict


def unseen_text(needed: float) -> str:
    return f"FAIL (could not see an IC this small: needed {needed:.4f})"


def window_result(window: str, ics: Mapping[date, DayIC], spans: Sequence[tuple[date, date]], h: int,
                  missing_share: float | None, cutoff: date | None = None, bar: float = T_BAR,
                  inclusive: bool = False, books: Mapping[date, Book] | None = None) -> WindowResult:
    """One window's result from the daily ICs: a day counts in its segment only when its exit day
    is inside the window too (nothing in it reads past its end). With `cutoff`, only later days
    count: a segment ending on or before it is labelled INSIDE, one across it is trimmed. `bar`
    is the t the verdict needs (`inclusive`: at it passes), and the t the power figures assume. `books`
    (top_books) give the turnover and break-even cost of the window's top-fifth book."""
    end = spans[-1][1] if spans else None
    rows, counted_days = [], []
    for s, e in spans:
        days = sorted(d for d, (_, _, x) in ics.items() if s <= d <= e and x <= end)
        why = None
        if cutoff is not None and e <= cutoff:
            why = INSIDE
        else:
            if cutoff is not None and s <= cutoff:
                days = [d for d in days if d > cutoff]
            if len(days) < MIN_DAYS:
                why = f"{len(days)} IC days (needs {MIN_DAYS})"
        vals = [ics[d][0] for d in days]
        rows.append(SegmentRow(s, e, _r(mean(vals)), _r(newey_west_t(vals, h)), len(vals), why is None, why))
        if why is None:
            counted_days += days
    vals = [ics[d][0] for d in counted_days]
    ic, t = mean(vals), newey_west_t(vals, h)
    v = verdict(ic, t, [SegmentIC(r.start, r.end, r.ic, r.t, r.days, r.counted, r.why) for r in rows], bar, inclusive)
    names = mean([ics[d][1] for d in counted_days])
    observed = bar * abs(ic / t) if ic is not None and t else None
    turnover, cost = (None, None)
    if books and spans:
        first = spans[0][0] if cutoff is None else max(spans[0][0], cutoff + timedelta(1))
        turnover, cost = book_trading(books, first, end)
    return WindowResult(window, spans[0][0] if spans else date.min, end or date.min, h, _r(ic), _r(t), len(vals),
                        _r(names, 1), round(len(vals) / h, 1),
                        _r(power(names, len(vals), h, bar)) if names else None, _r(observed), missing_share,
                        tuple(rows), v.label, v.passed, v.positive, v.counted, turnover, cost)


def overall(labels: Sequence[str]) -> str:
    """PASS only when every horizon passes; FAIL when any fails; else INCONCLUSIVE."""
    if labels and all(x == "PASS" for x in labels):
        return "PASS"
    return "FAIL" if "FAIL" in labels else "INCONCLUSIVE"


def _r(x: float | None, places: int = 4) -> float | None:
    return None if x is None else round(x, places)
