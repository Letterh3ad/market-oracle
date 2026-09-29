"""Event studies: how a listing's stored closes moved after its past earnings releases, raw
and against the benchmark. Stored data only, and nothing here feeds research or verdicts."""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from oracle.analysts.breaks import first_break, is_price
from oracle.market.bars import Bar
from oracle.research import edgar
from oracle.research.tools import ToolError
from oracle.services.context import AppContext
from oracle.services.http import open_client
from oracle.services.research_tools.filings import NoFilings, submissions
from oracle.services.research_tools.web import need_contact
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.sec import SecTickerRepo
from oracle.store.universe import UniverseRepo

HORIZONS = (1, 5, 20)
"""Trading days after the release."""
LAST = 8
"""How many of the latest measured releases the summary covers."""
MAX_AGE = timedelta(days=7)
RETRY_AFTER = timedelta(hours=1)
STALE = timedelta(days=7)
"""How far a close may sit from the day it stands for (holidays, halts)."""
SEC_SOURCE = "SEC 8-K item 2.02"
BREAK = "price series break, skipped"
NO_BASE = "no stored close near the date"
_KEY = "earnings_dates:"
_TRIED = "earnings_dates_failed:"


@dataclass(frozen=True)
class Window:
    days: int
    move: float | None
    """Ratio from the base close; None until that many closes are stored after it."""
    excess: float | None
    """The move minus the benchmark's over the same dates."""


@dataclass(frozen=True)
class EarningsEvent:
    day: date
    before_open: bool | None
    base: date | None
    """The close moves are measured from: the day's own, or the day before's for a release
    before the open."""
    windows: tuple[Window, ...]
    note: str | None = None


@dataclass(frozen=True)
class Summary:
    days: int
    n: int
    median: float | None
    up: int
    median_excess: float | None
    beat: int
    """Releases the listing beat the benchmark after."""


@dataclass(frozen=True)
class EarningsStudy:
    symbol: str
    source: str | None
    fetched_at: str | None
    benchmark: str | None
    events: tuple[EarningsEvent, ...]
    """Newest first."""
    summaries: tuple[Summary, ...]
    note: str | None = None
    """Why there are no dates, or why they may be out of date."""

    @property
    def headline(self) -> str | None:
        s = self.summaries[0] if self.summaries else None
        if s is None or not s.n:
            return None
        return f"after the last {s.n} earnings: median {s.median * 100:+.1f}% next day, {s.up} of {s.n} up"


async def earnings_study(ctx: AppContext, symbol: str, refresh: bool = True) -> EarningsStudy:
    """With `refresh`, release dates older than a week are fetched again first; a failure
    keeps the stored ones."""
    listing = resolve_symbol(ctx, symbol)
    ticker = UniverseRepo(ctx.conn).profile(listing.symbol).primary_symbol.upper()
    meta = MetaRepo(ctx.conn)
    stored = json.loads(meta.get(_KEY + ticker) or "null")
    note = None
    if "." in ticker:
        note = (f"no earnings dates source for {ticker}: SEC covers US listings only, and Yahoo gives "
                "only the next date")
    elif refresh and (stored is None or _age(ctx, stored["fetched_at"]) >= MAX_AGE):
        stored, note = await _refresh(ctx, meta, ticker, stored)
    if stored is None:
        return EarningsStudy(listing.symbol, None, None, None, (), (), note or "earnings dates not fetched yet")
    releases = [edgar.Release(date.fromisoformat(d), before) for d, before in stored["releases"]]
    bars = BarRepo(ctx.conn).history(listing.symbol)
    benchmark = next((b for b in ctx.config.compare.benchmarks if BarRepo(ctx.conn).last_date(b)), None)
    bench = BarRepo(ctx.conn).history(benchmark) if benchmark else []
    events = tuple(_event(r, bars, bench) for r in releases)
    return EarningsStudy(listing.symbol, stored["source"], stored["fetched_at"], benchmark, events,
                         tuple(_summary(events, i) for i in range(len(HORIZONS))), note)


def stored_releases(ctx: AppContext, symbol: str) -> tuple[edgar.Release, ...]:
    """The listing's earnings releases as last fetched, never fetching: none stored, none."""
    profile = UniverseRepo(ctx.conn).profile(symbol)
    if profile is None:
        return ()
    stored = json.loads(MetaRepo(ctx.conn).get(_KEY + profile.primary_symbol.upper()) or "null")
    return tuple(edgar.Release(date.fromisoformat(d), before) for d, before in stored["releases"]) if stored else ()


async def _refresh(ctx: AppContext, meta: MetaRepo, ticker: str, stored: dict | None) -> tuple[dict | None, str | None]:
    """Fresh dates, or the stored ones and why not. A failure is not retried within RETRY_AFTER."""
    try:
        need_contact(ctx.config.research.contact)
    except ToolError as exc:
        return stored, str(exc)
    tried = json.loads(meta.get(_TRIED + ticker) or "null")
    if tried and _age(ctx, tried["at"]) < RETRY_AFTER:
        return stored, tried["note"]
    try:
        fresh = await _fetch(ctx, ticker)
    except ToolError as exc:
        # The tools' own errors speak to the research model ("try another tool").
        note = str(exc) if isinstance(exc, NoFilings) else (
            f"could not reach SEC: kept the dates from {stored['fetched_at'][:10]}" if stored
            else "could not reach SEC, try later")
        meta.set(_TRIED + ticker, json.dumps({"at": to_utc_iso(ctx.clock.utcnow()), "note": note}))
        return stored, note
    meta.set(_KEY + ticker, json.dumps(fresh))
    return fresh, None


async def _fetch(ctx: AppContext, ticker: str) -> dict:
    async with open_client(ctx) as client:
        _, body = await submissions(client, SecTickerRepo(ctx.conn), ctx.clock, ctx.config.research.contact, ticker)
    releases = edgar.parse_earnings(body)
    return {"source": SEC_SOURCE, "fetched_at": to_utc_iso(ctx.clock.utcnow()),
            "releases": [[r.day.isoformat(), r.before_open] for r in releases]}


def _age(ctx: AppContext, fetched_at: str) -> timedelta:
    return ctx.clock.utcnow() - datetime.fromisoformat(fetched_at)


def _event(release: edgar.Release, bars: list[Bar], bench: list[Bar]) -> EarningsEvent:
    days = [b.date for b in bars]
    find = bisect_left if release.before_open else bisect_right
    i = find(days, release.day) - 1
    empty = tuple(Window(h, None, None) for h in HORIZONS)
    if i < 0 or release.day - days[i] > STALE:
        return EarningsEvent(release.day, release.before_open, None, empty, NO_BASE)
    span = bars[i:i + max(HORIZONS) + 1]
    if first_break([b.close for b in span]) is not None:
        return EarningsEvent(release.day, release.before_open, days[i], empty, BREAK)
    windows = []
    for h in HORIZONS:
        if i + h >= len(bars):
            windows.append(Window(h, None, None))
            continue
        move = _change(bars[i], bars[i + h])
        other = _bench_change(bench, days[i], days[i + h])
        windows.append(Window(h, move, move - other if other is not None else None))
    return EarningsEvent(release.day, release.before_open, days[i], tuple(windows))


def _change(a: Bar, b: Bar) -> float:
    # Adjusted closes only as a pair: one end adjusted and the other not would be a made-up move.
    if is_price(a.adj_close) and is_price(b.adj_close):
        return b.adj_close / a.adj_close - 1
    return b.close / a.close - 1


def _bench_change(bench: list[Bar], start: date, end: date) -> float | None:
    """The benchmark's move between its last closes on or before each day, if both are recent
    and the span between them is steady."""
    days = [b.date for b in bench]
    i, j = bisect_right(days, start) - 1, bisect_right(days, end) - 1
    # One bar for both days: the benchmark lags, so its move is unknown, not zero.
    if i < 0 or start - days[i] > STALE or end - days[j] > STALE or (j == i and end > start):
        return None
    span = bench[i:j + 1]
    if first_break([b.close for b in span]) is not None:
        return None
    return _change(bench[i], bench[j])


def _summary(events: tuple[EarningsEvent, ...], k: int) -> Summary:
    measured = [e.windows[k] for e in events if e.windows[k].move is not None][:LAST]
    moves = [w.move for w in measured]
    excess = [w.excess for w in measured if w.excess is not None]
    return Summary(HORIZONS[k], len(moves), median(moves) if moves else None, sum(m > 0 for m in moves),
                   median(excess) if excess else None, sum(x > 0 for x in excess))
