"""The signal testbed: S&P 500 members as they were on each day (fja05680/sp500, MIT), Yahoo
daily bars from 2010 for every member ever plus the SPY and ACWI benchmarks, and each member's
GICS sector, all in the research database (`research_db_path`). The live database is never
written: the build paces and locks itself on the research database too. Resumable (a member
tried is not asked again), and it steps aside for live work between requests.

Contracts for the signal harness: `open_testbed` gives a `Testbed` (membership on a day, spans,
bars, sectors, the trading calendar); `testbed_coverage` the missing share per year."""

from __future__ import annotations

import json
from bisect import bisect_left
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

from oracle.market.bars import Bar
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.market.yahoo import QuoteSummary, YahooPrices
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.services.context import AppContext
from oracle.services.errors import DatabaseTooNew, JobAlreadyRunning
from oracle.services.http import open_client
from oracle.services.jobs_service import Held, single_flight
from oracle.store.bars import BarRepo
from oracle.store.db import connect, to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.migrate import SchemaTooNew, migrate
from oracle.store.testbed import MemberRow, Snapshot, Span, TestbedRepo
from oracle.store.testbed_migrations import MIGRATIONS as TESTBED_MIGRATIONS
from oracle.universe import indexes as index_pages
from oracle.universe import sp500_history as sp500
from oracle.universe.indexes import BY_CODE, Unreadable

__all__ = ["BENCHMARKS", "CALENDAR", "JOB", "START", "Duplicate", "Testbed", "TestbedCoverage", "TestbedRun", "UnpricedMember",
           "YearCoverage", "build_testbed", "last_run", "open_testbed", "research_context", "testbed_coverage"]

START = date(2010, 1, 1)
BENCHMARKS = ("SPY", "ACWI")
CALENDAR = "SPY"
"""Its bar days are the trading calendar member-days are counted on."""
TAIL = timedelta(days=120)
"""Bars kept after a member's last day, for forward returns; later ones may be a reused ticker's."""
JOB = "testbed_build"
LEASE_S = 1800.0
WAIT_STEP_S = 5.0
FAILED_IN_A_ROW = 5
LAST_RUN = "testbed_last_run"
WIKIPEDIA, YAHOO = "wikipedia", "yahoo"
NOT_ON_YAHOO = "not on Yahoo"
NO_BARS_AS_MEMBER = "no bars while a member"
REUSED = "ticker reused by another instrument"
FIRST_TRADE_SLACK = timedelta(days=7)
"""Yahoo's first trade day may trail an index add made on a listing's first days."""
UNDER_PRICED = 0.5
"""Share of its member-days with bars below which a member is listed as under-priced."""
_FAILED = object()


@contextmanager
def research_context(ctx: AppContext) -> Iterator[AppContext]:
    """`ctx` over the research database (created and migrated on first use) instead of the live one."""
    conn = connect(ctx.config.research_db_path)
    try:
        try:
            migrate(conn, TESTBED_MIGRATIONS)
        except SchemaTooNew as exc:
            raise DatabaseTooNew(str(exc)) from exc
        yield replace(ctx, conn=conn)
    finally:
        conn.close()


class Testbed:
    """Reads of the built testbed. Tickers are in Yahoo's spelling (BRK-B), as the bars are keyed."""

    __test__ = False

    def __init__(self, ctx: AppContext) -> None:
        self._repo, self._bars = TestbedRepo(ctx.conn), BarRepo(ctx.conn)
        self._today = ctx.clock.utcnow().date()

    def members_on(self, day: date) -> frozenset[str]:
        """The index's members on `day`, as the snapshot lists them; empty before its first row."""
        return self._repo.members_on(day)

    def spans(self) -> list[Span]:
        return self._repo.spans()

    def bars(self, symbol: str, start: date, end: date | None = None) -> list[Bar]:
        """Oldest first, `start` to `end` (both included)."""
        return self._bars.since(symbol, start, end)

    def bars_between(self, start: date, end: date) -> dict[str, list[Bar]]:
        return self._bars.between_all(start, end)

    def sector(self, ticker: str) -> str | None:
        """Its GICS sector as known today (not as it was then); None when unknown."""
        return self._repo.sector(ticker)

    def sectors(self) -> dict[str, str | None]:
        return {m.ticker: m.sector for m in self._repo.members()}

    def calendar(self, start: date, end: date | None = None) -> list[date]:
        """Trading days: CALENDAR's bar days, or weekdays when it has none stored."""
        end = end or self._today
        days = self._repo.bar_days(CALENDAR, start, end)
        if days:
            return days
        return [d for d in (start + timedelta(n) for n in range((end - start).days + 1)) if d.weekday() < 5]

    def calendar_source(self, start: date, end: date | None = None) -> str:
        return CALENDAR if self._repo.bar_days(CALENDAR, start, end or self._today) else "weekdays"

    def snapshot(self) -> Snapshot | None:
        return self._repo.snapshot()


@contextmanager
def open_testbed(ctx: AppContext) -> Iterator[Testbed]:
    with research_context(ctx) as r:
        yield Testbed(r)


@dataclass
class TestbedRun:
    __test__ = False
    members: int = 0
    """Members in scope (a member at some point from the start)."""
    requests: int = 0
    snapshot: str | None = None
    """"fetched" or "kept" (younger than a refresh needs), or why it could not be read."""
    priced: int = 0
    not_on_yahoo: int = 0
    reused: int = 0
    bars_added: int = 0
    sectors_set: int = 0
    stopped: str | None = None
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0
    finished_at: str | None = None

    @property
    def summary(self) -> str:
        head = f"stopped early ({self.stopped})" if self.stopped else "finished"
        tail = f"; {len(self.failures)} failed, first: {self.failures[0]}" if self.failures else ""
        return (f"{head}: {self.members} members, {self.requests} requests, {self.priced} priced, "
                f"{self.not_on_yahoo} not on Yahoo, {self.reused} reused tickers, {self.bars_added} bars added, {self.sectors_set} sectors set{tail}")


class _Stopped(Exception):
    pass


async def build_testbed(ctx: AppContext, *, refresh: bool = False, only: Sequence[str] | None = None,
                        limit: int | None = None, start: date = START, end: date | None = None,
                        stop: Callable[[], bool] | None = None, busy: Callable[[], str | None] | None = None,
                        on_progress: Callable[[str], None] = lambda _: None) -> TestbedRun:
    """Fetch what the testbed lacks: the membership snapshot (again with `refresh`), sectors, and
    bars from `start` (to `end`) for each member not yet tried (with `refresh`, current members
    topped up too). `only` / `limit` narrow the members (a smoke run). `busy` names live work to
    wait for between requests; `stop` ends the run there."""
    began = time.monotonic()
    run = TestbedRun()
    with research_context(ctx) as r:
        try:
            with single_flight(r, JOB, LEASE_S) as held:
                b = _Build(r, held, run, start, end, stop, busy, on_progress)
                try:
                    async with open_client(r) as client:
                        b.client = client
                        await b.membership(refresh)
                        scope = b.scope(only, limit)
                        await b.wikipedia_sectors(refresh)
                        await b.benchmarks(refresh)
                        await b.bars(scope, refresh)
                        await b.yahoo_sectors(scope)
                except _Stopped:
                    pass
        except Exception as exc:
            run.stopped = f"failed: {type(exc).__name__}: {exc}"
            raise
        finally:
            # Kept even when the run raised, so the card shows this run, not the one before.
            run.seconds = round(time.monotonic() - began, 1)
            run.finished_at = to_utc_iso(ctx.clock.utcnow())
            MetaRepo(r.conn).set(LAST_RUN, json.dumps(asdict(run)))
    return run


def last_run(ctx: AppContext) -> TestbedRun | None:
    with research_context(ctx) as r:
        return _last_run(r)


def _last_run(r: AppContext) -> TestbedRun | None:
    raw = MetaRepo(r.conn).get(LAST_RUN)
    return TestbedRun(**json.loads(raw)) if raw else None


class _Build:
    client: RateLimitedClient

    def __init__(self, r: AppContext, held: Held, run: TestbedRun, start: date, end: date | None, stop, busy,
                 on_progress) -> None:
        self.r, self.held, self.run, self.start, self.end = r, held, run, start, end
        self.stop, self.busy, self.on_progress = stop, busy, on_progress
        self.repo, self.bar_repo = TestbedRepo(r.conn), BarRepo(r.conn)
        self.now = r.clock.utcnow()
        self.failed_in_a_row = 0
        # Bars of only part of the period: a member is not marked tried, so a full build fetches it whole.
        self.narrowed = start != START or end is not None

    async def membership(self, refresh: bool) -> None:
        if self.repo.snapshot() is not None and not refresh:
            self.run.snapshot = "kept"
            return
        await self._turn("reading the S&P 500 membership history")
        try:
            rev = await self._revision()
            url = sp500.FILE_URL.format(rev=rev or "master")
            resp = await self._get(sp500.SOURCE, url)
            rows = sp500.parse_history(resp.text)
        except (Unreadable, RequestFailed, CircuitOpen) as exc:
            self.run.snapshot = f"unreadable: {exc}"
            if self.repo.snapshot() is None:
                self.run.stopped = f"no membership history: {exc}"
                raise _Stopped from None
            return
        self.repo.save_snapshot(Snapshot(sp500.REPO, url, rev, self._at(), rows[0][0], rows[-1][0], len(rows)), rows)
        self.run.snapshot = "fetched"

    async def _revision(self) -> str | None:
        """The commit the file is read at, so the stored snapshot names its exact version."""
        try:
            resp = await self._get(sp500.SOURCE, sp500.COMMIT_URL, headers={"Accept": "application/vnd.github+json"})
            return resp.json().get("sha")
        except (RequestFailed, ValueError, AttributeError):
            return None

    def scope(self, only: Sequence[str] | None, limit: int | None) -> list[str]:
        """Members at some point from the start (to the end), alphabetical."""
        last = self.end or date.max
        tickers = sorted({s.ticker for s in self.repo.spans() if (s.end is None or s.end > self.start)
                          and s.start <= last})
        if only is not None:
            wanted = {sp500.yahoo_ticker(t) for t in only}
            tickers = [t for t in tickers if t in wanted]
        tickers = tickers[:limit] if limit is not None else tickers
        self.run.members = len(tickers)
        return tickers

    async def wikipedia_sectors(self, refresh: bool) -> None:
        if not refresh and any(m.sector_source == WIKIPEDIA for m in self.repo.members()):
            return
        await self._turn("reading today's GICS sectors from Wikipedia")
        try:
            resp = await self._get(index_pages.SOURCE, index_pages.page_url(BY_CODE["sp500"]),
                                   headers={"User-Agent": index_pages.USER_AGENT})
            sectors = sp500.parse_sectors(resp.text)
        except (Unreadable, RequestFailed, CircuitOpen) as exc:
            self.run.failures.append(f"Wikipedia sectors: {exc}")
            return
        known = {m.ticker for m in self.repo.members()}
        found = {t: s for t, s in sectors.items() if t in known}
        self.repo.set_sectors(found, WIKIPEDIA, self._at())
        self.run.sectors_set += len(found)

    async def benchmarks(self, refresh: bool) -> None:
        for symbol in BENCHMARKS:
            last = self.bar_repo.last_date(symbol)
            if last is not None and not refresh:
                continue
            await self._turn(f"benchmark bars: {symbol}")
            await self._guarded(symbol, self._benchmark(symbol, last + timedelta(days=1) if last else self.start))

    async def _benchmark(self, symbol: str, since: date) -> None:
        chart = await self._chart(symbol, since)
        if chart and chart is not _FAILED and chart.currency:
            self.run.bars_added += self.bar_repo.upsert(self._window(chart.bars, since, None), self._at())

    async def bars(self, scope: list[str], refresh: bool) -> None:
        members = {m.ticker: m for m in self.repo.members()}
        spans: dict[str, list[Span]] = {}
        for s in self.repo.spans():
            spans.setdefault(s.ticker, []).append(s)
        todo = [t for t in scope if members[t].bars_tried_at is None
                or (refresh and members[t].bars_note is None and any(s.end is None for s in spans[t]))]
        for i, t in enumerate(todo, 1):
            await self._turn(f"bars from {self.start}: {t} ({i}/{len(todo)})")
            await self._guarded(t, self._member(t, members[t], spans[t]))

    async def _member(self, t: str, m: MemberRow, spans: list[Span]) -> None:
        last = self.bar_repo.last_date(t) if m.bars_tried_at else None
        since = last + timedelta(days=1) if last else self.start
        ends = [s.end for s in spans]
        current = None in ends
        keep_to = None if current else max(ends) + TAIL
        chart = await self._chart(t, since)
        if chart is _FAILED:
            return
        kept: list[Bar] = []
        if not chart or not chart.bars:
            why = NOT_ON_YAHOO
        elif chart.currency is None or chart.instrument_type != "EQUITY":
            why = f"{REUSED}: Yahoo's {t} is a {chart.instrument_type or 'listing of no type'}"
        elif not current and chart.first_trade and chart.first_trade > _first_day(spans) + FIRST_TRADE_SLACK:
            why = f"{REUSED}: Yahoo's {t} first traded {chart.first_trade}, after it joined the index"
        else:
            kept = self._window(chart.bars, since, keep_to)
            why = None if kept else NO_BARS_AS_MEMBER
        reused = bool(why and why.startswith(REUSED))
        if why and last is not None and not reused:
            # Nothing newer yet (a weekend, before the close): the bars it has stand.
            self._note(t, m.bars_note)
            return
        if reused:
            self.repo.drop_bars(t)
        self.run.bars_added += self.bar_repo.upsert(kept, self._at())
        self.run.priced += bool(kept)
        self.run.not_on_yahoo += why == NOT_ON_YAHOO
        self.run.reused += reused
        self._note(t, why)

    def _note(self, t: str, why: str | None) -> None:
        if not self.narrowed:
            self.repo.note_bars(t, self._at(), why)

    def _window(self, bars: list[Bar], since: date, keep_to: date | None) -> list[Bar]:
        return [b for b in bars if since <= b.date and (keep_to is None or b.date <= keep_to)
                and (self.end is None or b.date <= self.end)]

    async def _chart(self, symbol: str, since: date):
        """The chart from `since`, None when Yahoo has nothing, or _FAILED after noting why."""
        upto = self._until()
        if since > upto.date():
            return None
        try:
            self.run.requests += 1
            chart = await YahooPrices(self.client).daily_chart(Instrument(symbol, None), since, upto)
        except NoData:
            chart = None
        except SourceFailed as exc:
            # Yahoo answers 400 "Data doesn't exist for startDate" for a ticker with nothing then.
            if exc.status != 400:
                self._failed(f"{symbol} bars: {exc}")
                return _FAILED
            chart = None
        self.failed_in_a_row = 0
        return chart

    async def _guarded(self, symbol: str, step) -> None:
        """One ticker's step: an unexpected answer is that ticker's failure, never the run's end."""
        try:
            await step
        except (_Stopped, JobAlreadyRunning):
            raise
        except Exception as exc:
            self._failed(f"{symbol} bars: {type(exc).__name__}: {exc}")

    async def yahoo_sectors(self, scope: list[str]) -> None:
        """Members Wikipedia's table lacks (former ones), from Yahoo's profile, in GICS's words."""
        members = {m.ticker: m for m in self.repo.members()}
        todo = [t for t in scope if members[t].sector is None and members[t].sector_tried_at is None
                and members[t].bars_tried_at is not None and members[t].bars_note is None]
        summary = QuoteSummary(self.client)
        for i, t in enumerate(todo, 1):
            await self._turn(f"sectors from Yahoo: {t} ({i}/{len(todo)})")
            try:
                self.run.requests += 1
                profile = (await summary.modules(t, "assetProfile")).get("assetProfile") or {}
            except NoData:
                profile = {}
            except (SourceFailed, KeyError, IndexError, TypeError, ValueError) as exc:
                self._failed(f"{t} sector: {exc}")
                continue
            self.failed_in_a_row = 0
            sector = sp500.YAHOO_TO_GICS.get(profile.get("sector") or "")
            if sector:
                self.repo.set_sectors({t: sector}, YAHOO, self._at())
                self.run.sectors_set += 1
            else:
                self.repo.note_sector_try(t, self._at())

    def _failed(self, why: str) -> None:
        self.run.failures.append(why)
        self.failed_in_a_row += 1
        if self.failed_in_a_row >= FAILED_IN_A_ROW:
            self.run.stopped = f"{FAILED_IN_A_ROW} requests failed in a row; run it again to resume"
            raise _Stopped

    async def _get(self, source: str, url: str, headers: dict | None = None):
        self.run.requests += 1
        resp = await self.client.get(source, url, headers=headers)
        if resp.status_code != 200:
            raise RequestFailed(source, resp.status_code, f"HTTP {resp.status_code}")
        return resp

    def _until(self) -> datetime:
        if self.end is None:
            return self.now
        return datetime.combine(self.end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    async def _turn(self, label: str) -> None:
        """Between requests: stop when told, and wait while live work runs (or is about to)."""
        self._check()
        while self.busy and (other := self.busy()):
            self.on_progress(f"waiting for {other}")
            await self.r.clock.sleep(WAIT_STEP_S)
            self._check()
        self.on_progress(f"{label}; {self.run.requests} requests")

    def _check(self) -> None:
        self.held.check()
        if self.stop and self.stop():
            self.run.stopped = "cancelled"
            raise _Stopped

    def _at(self) -> str:
        return to_utc_iso(self.r.clock.utcnow())


@dataclass(frozen=True)
class YearCoverage:
    year: int
    members: int
    """Tickers that were members on some trading day of the year."""
    member_days: int
    with_bars: int
    unpriced: int
    """Members with no bar on any of their member-days that year."""

    @property
    def missing_share(self) -> float:
        return round(1 - self.with_bars / self.member_days, 4) if self.member_days else 0.0


@dataclass(frozen=True)
class UnpricedMember:
    ticker: str
    first_day: date
    """Its first and last member trading day from the start."""
    last_day: date
    member_days: int
    with_bars: int
    note: str
    """Why: not on Yahoo, a reused ticker, no bars while a member, not fetched yet, or how few days it has."""


@dataclass(frozen=True)
class Duplicate:
    """One company listed under two tickers on the same days: a cross-section should keep one."""

    tickers: tuple[str, str]
    first_day: date
    last_day: date
    days: int
    """Trading days both are members."""
    why: str


@dataclass(frozen=True)
class TestbedCoverage:
    __test__ = False
    start: date
    years: tuple[YearCoverage, ...]
    under_priced: tuple[UnpricedMember, ...]
    """Members with bars on under UNDER_PRICED of their member-days, none at all included."""
    duplicates: tuple[Duplicate, ...]
    members: int
    fetched: int
    """Members whose bars were asked for."""
    sectors: int
    """Members with a sector."""
    calendar: str
    snapshot: Snapshot | None
    last_run: TestbedRun | None
    as_of: date
    """Membership after the snapshot's last change repeats that day's list up to this day."""
    caveat: str = ("delisted members and members whose ticker later changed are mostly not on Yahoo (counted as "
                   "missing); sectors are today's GICS, not as they were")

    @property
    def missing_share(self) -> float:
        days = sum(y.member_days for y in self.years)
        return round(1 - sum(y.with_bars for y in self.years) / days, 4) if days else 0.0


def testbed_coverage(ctx: AppContext, start: date = START) -> TestbedCoverage:
    """Member-days with and without bars per year, and the members never priced, reading only."""
    with research_context(ctx) as r:
        tb, repo = Testbed(r), TestbedRepo(r.conn)
        calendar = tb.calendar(start)
        spans = [s for s in repo.spans() if s.end is None or s.end > start]
        cells = repo.year_tickers(spans, calendar)
        members = {m.ticker: m for m in repo.members()}
        years: dict[int, list] = {}
        per_ticker: dict[str, list[int]] = {}
        for c in cells:
            years.setdefault(c.year, []).append(c)
            t = per_ticker.setdefault(c.ticker, [0, 0])
            t[0] += c.member_days
            t[1] += c.with_bars
        low = {t for t, (n, priced) in per_ticker.items() if priced < UNDER_PRICED * n}
        first_last = _first_last([s for s in spans if s.ticker in low], calendar)
        under = tuple(UnpricedMember(t, *first_last[t], *per_ticker[t], _why(members.get(t), *per_ticker[t]))
                      for t in sorted(low))
        in_scope = set(per_ticker)
        return TestbedCoverage(
            start,
            tuple(YearCoverage(y, len(cs), sum(c.member_days for c in cs), sum(c.with_bars for c in cs),
                               sum(c.with_bars == 0 for c in cs)) for y, cs in sorted(years.items())),
            under, _duplicates(spans, calendar), len(in_scope),
            sum(members[t].bars_tried_at is not None for t in in_scope if t in members),
            sum(members[t].sector is not None for t in in_scope if t in members),
            tb.calendar_source(start), repo.snapshot(), _last_run(r), r.clock.utcnow().date())


def _first_last(spans: list[Span], calendar: list[date]) -> dict[str, tuple[date, date]]:
    """Each ticker's first and last member trading day on `calendar`."""
    out: dict[str, tuple[date, date]] = {}
    for s in spans:
        lo, hi = bisect_left(calendar, s.start), bisect_left(calendar, s.end or date.max)
        if lo < hi:
            first, last = out.get(s.ticker, (calendar[lo], calendar[hi - 1]))
            out[s.ticker] = (min(first, calendar[lo]), max(last, calendar[hi - 1]))
    return out


def _why(m: MemberRow | None, member_days: int, with_bars: int) -> str:
    if with_bars:
        return f"priced on {with_bars} of {member_days} member-days"
    if m is None or m.bars_tried_at is None:
        return "not fetched yet"
    return m.bars_note or NO_BARS_AS_MEMBER


# One company under two tickers on the same days in the source (checked by hand at gate 01, 2026-10-01). A
# general check (same company by SEC CIK) belongs with the rename map (ticket 01b).
KNOWN_DUPLICATES = (
    (("KORS", "CPRI"), "Michael Kors Holdings listed again as Capri Holdings"),
    (("CCE", "CCEP"), "Coca-Cola Enterprises listed again as Coca-Cola Europacific Partners"),
)


def _duplicates(spans: list[Span], calendar: list[date]) -> tuple[Duplicate, ...]:
    out = []
    for (a, b), why in KNOWN_DUPLICATES:
        both = sorted(_days(spans, a, calendar) & _days(spans, b, calendar))
        if both:
            out.append(Duplicate((a, b), both[0], both[-1], len(both), why))
    return tuple(out)


def _days(spans: list[Span], ticker: str, calendar: list[date]) -> set[date]:
    out: set[date] = set()
    for s in spans:
        if s.ticker == ticker:
            out.update(calendar[bisect_left(calendar, s.start):bisect_left(calendar, s.end or date.max)])
    return out


def _first_day(spans: list[Span]) -> date:
    """Its first member day from the start."""
    return max(min(s.start for s in spans), START)
