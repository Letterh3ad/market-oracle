"""The pre-registered trend overlay test (boring-plan 04, DECISIONS 2026-10-01): the 10-month moving
average rule on the S&P 500 total-return index (^SP500TR), chosen on 1988-2007 (reported only) and
judged on 2008 to today against buy and hold. The series lives in the research database, never the
live one; each result is kept there beside the signal tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date

from oracle.broker.fills import COSTS
from oracle.broker.trend_overlay import (MONTHS, CrashRow, Run, Session, crash_rows, figures, judge, simulate)
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.market.yahoo import YahooPrices
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.services.context import AppContext
from oracle.services.errors import NoPrices
from oracle.services.http import open_client
from oracle.services.lab_registry import check_committed, require_committed
from oracle.services.testbed_service import research_context
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.signal_tests import SignalTestRepo

__all__ = ["CASH_NONE", "CHOOSE_END", "CHOOSE_START", "CONFIRM_START", "CRASHES", "KEY", "RULE", "SERIES", "TBILL",
           "TrendLine", "TrendSeries", "TrendTest", "TrendWindow", "run_and_keep_trend", "run_trend",
           "trend_sessions", "trend_tests", "update_trend_series"]

KEY = "trend_overlay"
CHOOSE_START, CHOOSE_END = date(1988, 1, 1), date(2007, 12, 31)
CONFIRM_START = date(2008, 1, 1)
FIRST_FETCH = date(1985, 1, 1)
"""Before Yahoo's first ^SP500TR day (1988-01-04), so a first fetch gets it all."""
TBILL = "^IRX"
"""The 13-week T-bill yield in percent. Only used when already stored: the rule never fetches it."""
CASH_NONE = f"0: no T-bill series ({TBILL}) is stored in the research database"
CRASHES = (("2008", date(2008, 1, 1), date(2009, 12, 31)), ("2020", date(2020, 1, 1), date(2020, 12, 31)),
           ("2022", date(2022, 1, 1), date(2022, 12, 31)))
"""Fixed before any data was read: calendar spans, so each counts the fall and the way back."""
RULE = (f"At each month end, compare the total-return series' close with its {MONTHS}-month simple moving "
        "average of month-end closes: above or equal, hold the index; below, hold cash from the next session's "
        f"open. {COSTS.slippage:.1%} of the value per switch (the backtest's slippage); no tax (ISA). PASS on "
        "confirm only when the worst fall is at most two thirds of buy and hold's and the yearly return at most "
        "1 point below it. Never tuned.")


@dataclass(frozen=True)
class TrendSeries:
    symbol: str
    label: str
    judged: bool
    """Only one series gives the verdict; the others are shown beside it."""


SERIES = TrendSeries("^SP500TR", "S&P 500 total return (USD)", True)


@dataclass(frozen=True)
class TrendLine:
    name: str
    label: str
    ret: float
    cagr: float
    max_fall: float
    cash_share: float
    points: tuple[tuple[date, float], ...]
    """Return since the window's first close: that close, each month end and the last session."""


@dataclass(frozen=True)
class TrendWindow:
    window: str
    start: date
    end: date
    """The first and last session inside it."""
    trend: TrendLine
    held: TrendLine
    switches: int
    fall_cut: float
    given_up: float
    verdict: str | None
    """PASS or FAIL on confirm; None on choose (reported only)."""
    crashes: tuple[CrashRow, ...]


@dataclass(frozen=True)
class TrendTest:
    series: str
    label: str
    rule: str
    cash: str
    """What cash earned: CASH_NONE or the stored T-bill series."""
    cost: float
    choose: TrendWindow
    confirm: TrendWindow
    verdict: str
    passed: bool
    first_day: date
    last_day: date
    run_at: str
    notes: tuple[str, ...]
    beside: tuple[TrendWindow, ...] = ()
    """Other series' confirm windows, shown not judged (the GBP ACWI proxy once fund comparison lands)."""
    id: int | None = None


def trend_sessions(r: AppContext, series: TrendSeries = SERIES) -> list[Session]:
    """The series' daily sessions from the research database: the one place a total-return
    series is read, so a shared total-return loader can replace it."""
    return [Session(b.date, b.open, b.close) for b in BarRepo(r.conn).history(series.symbol)]


def _cash_yield(r: AppContext) -> dict[date, float] | None:
    rows = BarRepo(r.conn).history(TBILL)
    return {b.date: b.close for b in rows} or None


async def update_trend_series(ctx: AppContext, series: TrendSeries = SERIES) -> str | None:
    """Fetch the series from its last stored day on into the research database (that day again, as
    it may have been stored mid-session); None when done, else why it could not."""
    with research_context(ctx) as r:
        repo = BarRepo(r.conn)
        last = repo.last_date(series.symbol)
        try:
            async with open_client(r) as client:
                chart = await YahooPrices(client).daily_chart(
                    Instrument(series.symbol, None), last or FIRST_FETCH,
                    ctx.clock.utcnow())
        except NoData:
            return None
        except (SourceFailed, RequestFailed, CircuitOpen) as exc:
            return f"could not update {series.symbol}: {exc}"
        repo.upsert(chart.bars, to_utc_iso(ctx.clock.utcnow()))
    return None


def _line(name: str, label: str, series: list[tuple[date, float]], in_cash: list[bool]) -> TrendLine:
    f = figures(series, in_cash)
    points = tuple((d, v - 1) for i, ((d, v), nxt) in enumerate(zip(series, [*series[1:], None]))
                   if i == 0 or nxt is None or (nxt[0].year, nxt[0].month) != (d.year, d.month))
    return TrendLine(name, label, f.ret, f.cagr, f.max_fall, f.cash_share, points)


def _window(name: str, run: Run, judged: bool, label: str) -> TrendWindow:
    trend = _line("trend_overlay", f"Trend overlay ({label})", run.equity, run.in_cash)
    held = _line("held", f"Buy and hold ({label})", run.held, [False] * len(run.held))
    j = judge(trend.max_fall, held.max_fall, trend.cagr, held.cagr)
    verdict = ("PASS" if j.passed else "FAIL") if judged else None
    return TrendWindow(name, run.equity[0][0], run.equity[-1][0], trend, held, run.switches, j.fall_cut, j.given_up,
                       verdict, tuple(crash_rows(run.equity, run.held, run.in_cash, CRASHES)))


def run_trend(ctx: AppContext, series: TrendSeries = SERIES) -> TrendTest:
    """Both windows from what is stored, up to the session before the run day (the run day's close
    may not be final). Choose sees no session after 2007; each window's first state comes from the
    month ends before it, so no session inside it decides its own start."""
    today = ctx.clock.utcnow().date()
    with research_context(ctx) as r:
        require_committed(r, [KEY])
        sessions = [s for s in trend_sessions(r, series) if s.day < today]
        cash = _cash_yield(r)
    if not any(s.day >= CONFIRM_START for s in sessions) or not any(s.day <= CHOOSE_END for s in sessions):
        raise NoPrices(f"no stored {series.symbol} history from 1988 to after 2008: update the trend series first")
    kw = {"cost": COSTS.slippage, "cash_yield": cash}
    choose = simulate([s for s in sessions if s.day <= CHOOSE_END], CHOOSE_START, CHOOSE_END, **kw)
    confirm = simulate(sessions, CONFIRM_START, today, **kw)
    cw = _window("confirm", confirm, series.judged, series.label)
    return TrendTest(series.symbol, series.label, RULE, CASH_NONE if cash is None else _cash_text(cash),
                     COSTS.slippage, _window("choose", choose, False, series.label), cw, cw.verdict or "SHOWN",
                     cw.verdict == "PASS", sessions[0].day, sessions[-1].day, to_utc_iso(ctx.clock.utcnow()),
                     _notes(sessions))


def _cash_text(cash: dict[date, float]) -> str:
    first, last = min(cash), max(cash)
    return (f"the 13-week T-bill yield ({TBILL}), stored {first.isoformat()} to {last.isoformat()}; 0 on days "
            "before its first")


def _notes(sessions: list[Session]) -> tuple[str, ...]:
    flat = [s.day for s in sessions if s.open is not None and s.open == s.close]
    stale = [s.day for p, s in zip(sessions, sessions[1:]) if s.open is not None and s.open == p.close != s.close]
    out = ["A month end is a month's last stored session. Before the rule has 10 month ends it holds the index.",
           "Buy and hold carries no costs; the overlay pays only on switches, not to take its first state.",
           "No dealing fee or platform charge here: those are the user's own (fees calculator).",
           "Each crash span runs from the close before it, or from the window's first close when the span starts "
           "with the window (2008 in confirm)."]
    if flat:
        out.append(f"{len(flat)} sessions ({flat[0]} to {flat[-1]}) have no real open (stored open equals the "
                   "close): a switch after one of those month ends fills at that session's close, a day late, "
                   "never earlier than the data allows.")
    if stale:
        out.append(f"{len(stale)} sessions ({stale[0]} to {stale[-1]}) open exactly at the previous close, likely a "
                   "stale open: a switch there fills at the previous close, missing the overnight gap.")
    return tuple(out)


def keep_trend(ctx: AppContext, t: TrendTest) -> TrendTest:
    raw = json.dumps(asdict(t), default=str)
    with research_context(ctx) as r:
        new_id = SignalTestRepo(r.conn).add(KEY, t.verdict, raw, t.run_at)
    return _load(raw, new_id)


async def run_and_keep_trend(ctx: AppContext, say: Callable[[str], None] | None = None) -> TrendTest:
    """Update the series, run the test and keep it. A failed update is a note when days are stored."""
    check_committed(ctx, [KEY])
    (say or (lambda _: None))(f"updating {SERIES.symbol}")
    failed = await update_trend_series(ctx)
    (say or (lambda _: None))("running the trend overlay test")
    t = run_trend(ctx)
    if failed:
        t = replace(t, notes=(*t.notes, failed[0].upper() + failed[1:] + "; the stored days were used."))
    return keep_trend(ctx, t)


def trend_tests(ctx: AppContext) -> TrendTest | None:
    """The newest kept result."""
    with research_context(ctx) as r:
        row = next((x for x in SignalTestRepo(r.conn).latest() if x.signal == KEY), None)
    return None if row is None else _load(row.result, row.id)


def _load(raw: str, row_id: int) -> TrendTest:
    d = json.loads(raw)
    day = date.fromisoformat

    def line(x: dict) -> TrendLine:
        return TrendLine(**{**x, "points": tuple((day(a), v) for a, v in x["points"])})

    def window(w: dict) -> TrendWindow:
        crashes = tuple(CrashRow(**{**c, "start": day(c["start"]), "end": day(c["end"])}) for c in w["crashes"])
        return TrendWindow(**{**w, "start": day(w["start"]), "end": day(w["end"]), "trend": line(w["trend"]),
                              "held": line(w["held"]), "crashes": crashes})

    return TrendTest(**{**d, "choose": window(d["choose"]), "confirm": window(d["confirm"]),
                        "beside": tuple(window(w) for w in d["beside"]), "first_day": day(d["first_day"]),
                        "last_day": day(d["last_day"]), "notes": tuple(d["notes"]), "id": row_id})
