"""The pre-registered trend overlay (DECISIONS 2026-10-01): at each month end, hold the index when
its close is at or above the 10-month simple average of month-end closes, else cash from the next
session's open. Numbers in, numbers out: sessions in, equity lines and figures out."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.broker.compare import max_fall

MONTHS = 10
PASS_FALL_CUT = 1 / 3
PASS_GIVEN_UP = 0.01
TRADING_DAYS = 252
_EPS = 1e-9
"""Float slack so a result exactly on a PASS bound is judged as written."""


@dataclass(frozen=True)
class Session:
    day: date
    open: float | None
    """None (or not above 0) when the source has no open: a switch then fills at the close."""
    close: float


def month_end_decisions(sessions: Sequence[Session], months: int = MONTHS) -> list[tuple[date, bool | None]]:
    """Each month end (a month's last session, known once a later month has one) with the rule's
    call from that close and the month ends before it: True hold, False cash, None too few months."""
    ends = [a for a, b in zip(sessions, sessions[1:]) if (a.day.year, a.day.month) != (b.day.year, b.day.month)]
    out = []
    for i, s in enumerate(ends):
        if i + 1 < months:
            out.append((s.day, None))
        else:
            window = ends[i + 1 - months:i + 1]
            out.append((s.day, s.close >= sum(x.close for x in window) / months))
    return out


@dataclass(frozen=True)
class Run:
    equity: list[tuple[date, float]]
    """The overlay's value at each session's close, 1.0 at the first."""
    held: list[tuple[date, float]]
    """Buy and hold of the same series, no costs."""
    in_cash: list[bool]
    """Per session, at its close."""
    switches: int
    cost: float

    @property
    def sessions(self) -> int:
        return len(self.equity)

    @property
    def cash_sessions(self) -> int:
        return sum(self.in_cash)


def simulate(sessions: Sequence[Session], start: date, end: date, cost: float,
             cash_yield: Mapping[date, float] | None = None) -> Run:
    """The overlay and buy and hold over the sessions from `start` to `end`. The state on the first
    session is the call of the last month end before it (hold while the rule has too few months);
    no cost to take it. Each switch costs `cost` of the value. Cash earns nothing, or with
    `cash_yield` (annual percent by day) the latest yield known at the previous close."""
    calls = dict(month_end_decisions(sessions))
    window = [i for i, s in enumerate(sessions) if start <= s.day <= end]
    if not window:
        return Run([], [], [], 0, cost)
    first = window[0]
    hold = next((calls[s.day] for s in reversed(sessions[:first]) if s.day in calls), None) is not False
    yields = sorted((cash_yield or {}).items())
    value, equity, in_cash, switches = 1.0, [(sessions[first].day, 1.0)], [not hold], 0
    for i in window[1:]:
        prev, s = sessions[i - 1], sessions[i]
        call = calls.get(prev.day)
        want = hold if call is None else call
        fill = s.open if s.open and s.open > 0 else s.close
        if want == hold:
            value *= s.close / prev.close if hold else 1 + _cash(yields, prev.day)
        elif hold:
            value *= fill / prev.close * (1 - cost)
        else:
            value *= (1 - cost) * s.close / fill
        switches += want != hold
        hold = want
        equity.append((s.day, value))
        in_cash.append(not hold)
    base = sessions[first].close
    held = [(sessions[i].day, sessions[i].close / base) for i in window]
    return Run(equity, held, in_cash, switches, cost)


def _cash(yields: list[tuple[date, float]], day: date) -> float:
    k = bisect_right(yields, day, key=lambda x: x[0])
    return (1 + yields[k - 1][1] / 100) ** (1 / TRADING_DAYS) - 1 if k else 0.0


@dataclass(frozen=True)
class Figures:
    ret: float
    cagr: float
    """Yearly return, compounded over calendar days."""
    max_fall: float
    cash_share: float
    """Share of sessions ending in cash."""


def figures(series: Sequence[tuple[date, float]], in_cash: Sequence[bool] = ()) -> Figures:
    first, last = series[0], series[-1]
    ret = last[1] / first[1] - 1
    days = (last[0] - first[0]).days
    cagr = (1 + ret) ** (365.25 / days) - 1 if days > 0 else 0.0
    return Figures(ret, cagr, max_fall(series), sum(in_cash) / len(in_cash) if in_cash else 0.0)


@dataclass(frozen=True)
class Judgement:
    fall_cut: float
    """1 - overlay worst fall / buy-and-hold worst fall."""
    given_up: float
    """Buy-and-hold CAGR minus the overlay's, as a ratio (0.01 is one point)."""
    passed: bool


def judge(trend_fall: float, held_fall: float, trend_cagr: float, held_cagr: float) -> Judgement:
    """PASS needs the worst fall at most two thirds of buy and hold's and at most one point a
    year given up; both, as pre-registered."""
    cut = 1 - trend_fall / held_fall if held_fall else 0.0
    given_up = held_cagr - trend_cagr
    return Judgement(cut, given_up, cut >= PASS_FALL_CUT - _EPS and given_up <= PASS_GIVEN_UP + _EPS)


@dataclass(frozen=True)
class CrashRow:
    name: str
    start: date
    end: date
    """The first and last session of the span inside the window."""
    trend_return: float
    held_return: float
    trend_fall: float
    held_fall: float
    cash_share: float


def crash_rows(equity: Sequence[tuple[date, float]], held: Sequence[tuple[date, float]], in_cash: Sequence[bool],
               crashes: Sequence[tuple[str, date, date]]) -> list[CrashRow]:
    """Each crash span with sessions in the window: each line's return from the close before the
    span (or its first) to the span's last, its worst fall inside, and the overlay's time in cash."""
    out = []
    for name, a, b in crashes:
        idx = [i for i, (d, _) in enumerate(equity) if a <= d <= b]
        if not idx:
            continue
        lo, hi = max(idx[0] - 1, 0), idx[-1]
        out.append(CrashRow(name, equity[idx[0]][0], equity[hi][0],
                            equity[hi][1] / equity[lo][1] - 1, held[hi][1] / held[lo][1] - 1,
                            max_fall(equity[lo:hi + 1]), max_fall(held[lo:hi + 1]),
                            sum(in_cash[i] for i in idx) / len(idx)))
    return out
