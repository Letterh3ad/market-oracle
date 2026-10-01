"""SEC XBRL fundamentals of the testbed's members (next-signals 04), point in time, in the research
database: SEC's ticker map to a CIK, each company's `companyfacts` (the concepts the fundamental
signals read), its periodic reports' acceptance times from its submissions, and Yahoo's split
events (per-share figures go into today's shares, like the prices). Resumable (a member tried is
not asked again) and it steps aside for live work between requests.

Then the three fundamental signals the registry runs: SUE (60 days), gross profitability and
earnings yield (20 days), each a score held from the day after its filing was accepted."""

from __future__ import annotations

import json
import time
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from oracle.analysts.signal_test import Panel, Signal
from oracle.market.sources import Instrument, NoData
from oracle.market.yahoo import YahooPrices
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research import edgar, xbrl
from oracle.research.xbrl import Event
from oracle.services.context import AppContext
from oracle.services.errors import JobAlreadyRunning
from oracle.services.http import open_client
from oracle.services.jobs_service import Held, single_flight
from oracle.services.testbed_service import START, research_context
from oracle.store.db import to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.testbed import TestbedRepo
from oracle.store.xbrl import XbrlRepo

__all__ = ["EARNINGS_YIELD", "FUNDAMENTAL_SIGNALS", "GROSS_PROFITABILITY", "JOB", "SUE", "XbrlCoverage", "XbrlRun",
           "fetch_fundamentals", "fundamentals_coverage", "last_xbrl_run", "timelines"]

JOB = "xbrl_fetch"
LEASE_S = 1800.0
WAIT_STEP_S = 5.0
FAILED_IN_A_ROW = 5
LAST_RUN = "xbrl_last_run"
SEC = "sec_edgar"
TICKERS_MAX_AGE = timedelta(days=7)
SINCE = date(2009, 1, 1)
"""Acceptance times and splits from here: the SUE history before the testbed's first day."""
NO_CIK = "no CIK in SEC's ticker map"
NO_FACTS = "SEC has no XBRL facts"


@dataclass
class XbrlRun:
    members: int = 0
    """Priced testbed members in scope."""
    requests: int = 0
    mapped: int = 0
    no_cik: int = 0
    with_facts: int = 0
    no_facts: int = 0
    facts_added: int = 0
    splits: int = 0
    stopped: str | None = None
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0
    finished_at: str | None = None

    @property
    def summary(self) -> str:
        head = f"stopped early ({self.stopped})" if self.stopped else "finished"
        tail = f"; {len(self.failures)} failed, first: {self.failures[0]}" if self.failures else ""
        return (f"{head}: {self.members} members, {self.requests} requests, {self.mapped} with a CIK, "
                f"{self.no_cik} without, {self.with_facts} with facts, {self.no_facts} without, "
                f"{self.facts_added} facts stored, {self.splits} split histories{tail}")


class _Stopped(Exception):
    pass


async def fetch_fundamentals(ctx: AppContext, *, refresh: bool = False, only: Sequence[str] | None = None,
                             limit: int | None = None, stop: Callable[[], bool] | None = None,
                             busy: Callable[[], str | None] | None = None,
                             on_progress: Callable[[str], None] = lambda _: None) -> XbrlRun:
    """Fetch what the research database lacks for the testbed's priced members: the CIK, the facts
    and acceptance times, the splits (every member again with `refresh`). `only` / `limit` narrow
    the members. `busy` names live work to wait for between requests; `stop` ends the run there."""
    began = time.monotonic()
    run = XbrlRun()
    with research_context(ctx) as r:
        try:
            with single_flight(r, JOB, LEASE_S) as held:
                f = _Fetch(r, held, run, stop, busy, on_progress)
                try:
                    async with open_client(r) as client:
                        f.client = client
                        await f.ticker_map(refresh)
                        await f.members(f.scope(only, limit), refresh)
                except _Stopped:
                    pass
        except Exception as exc:
            run.stopped = f"failed: {type(exc).__name__}: {exc}"
            raise
        finally:
            run.seconds = round(time.monotonic() - began, 1)
            run.finished_at = to_utc_iso(ctx.clock.utcnow())
            MetaRepo(r.conn).set(LAST_RUN, json.dumps(asdict(run)))
    return run


def last_xbrl_run(ctx: AppContext) -> XbrlRun | None:
    with research_context(ctx) as r:
        return _last_run(r)


def _last_run(r: AppContext) -> XbrlRun | None:
    raw = MetaRepo(r.conn).get(LAST_RUN)
    return XbrlRun(**json.loads(raw)) if raw else None


def _priced(r: AppContext) -> list[str]:
    return [m.ticker for m in TestbedRepo(r.conn).members() if m.bars_tried_at and m.bars_note is None]


def _lookup(repo: XbrlRepo, ticker: str) -> int | None:
    """SEC spells share classes with a dash like Yahoo (BRK-B); some with a dot or none."""
    for t in (ticker, ticker.replace("-", "."), ticker.replace("-", "")):
        if (cik := repo.cik(t)) is not None:
            return cik
    return None


class _Fetch:
    client: RateLimitedClient

    def __init__(self, r: AppContext, held: Held, run: XbrlRun, stop, busy, on_progress) -> None:
        self.r, self.held, self.run, self.stop, self.busy, self.on_progress = r, held, run, stop, busy, on_progress
        self.repo = XbrlRepo(r.conn)
        self.contact = r.config.research.contact
        self.failed_in_a_row = 0
        self.done: dict[int, str | None] = {}
        """CIKs fetched this run (share classes share one) -> their note."""

    @property
    def headers(self) -> dict:
        if not self.contact:
            self.run.stopped = "SEC needs a contact: set the research contact (name and email) in the settings"
            raise _Stopped
        return {"User-Agent": edgar.user_agent(self.contact)}

    async def ticker_map(self, refresh: bool) -> None:
        at = self.repo.tickers_fetched_at()
        if at and not refresh and self.r.clock.utcnow() - datetime.fromisoformat(at) < TICKERS_MAX_AGE:
            return
        await self._turn("reading SEC's ticker map")
        try:
            found = edgar.parse_tickers(await self._json(edgar.TICKERS_URL))
        except (RequestFailed, CircuitOpen, ValueError) as exc:
            if at is None:
                self.run.stopped = f"no ticker map from SEC: {exc}"
                raise _Stopped from None
            self.run.failures.append(f"ticker map: {exc}")
            return
        if found:
            self.repo.replace_tickers(found, self._at())

    def scope(self, only: Sequence[str] | None, limit: int | None) -> list[str]:
        tickers = _priced(self.r)
        if only is not None:
            wanted = {t.upper().replace(".", "-") for t in only}
            tickers = [t for t in tickers if t in wanted]
        tickers = tickers[:limit] if limit is not None else tickers
        self.run.members = len(tickers)
        return tickers

    async def members(self, scope: list[str], refresh: bool) -> None:
        known = self.repo.members()
        for i, t in enumerate(scope, 1):
            m = known.get(t)
            cik = _lookup(self.repo, t)
            self.run.mapped += cik is not None
            self.run.no_cik += cik is None
            if refresh or m is None or m.facts_tried_at is None:
                await self._turn(f"SEC facts: {t} ({i}/{len(scope)})")
                await self._guarded(t, self._facts(t, cik))
            if cik is not None and (refresh or m is None or m.splits_tried_at is None):
                await self._turn(f"splits: {t} ({i}/{len(scope)})")
                await self._guarded(t, self._splits(t))

    async def _facts(self, t: str, cik: int | None) -> None:
        if cik is None:
            self.repo.note_facts(t, None, self._at(), NO_CIK)
            return
        if cik in self.done:
            note = self.done[cik]
        else:
            body = await self._json(xbrl.COMPANYFACTS.format(cik=cik), missing_ok=True)
            facts = xbrl.parse_companyfacts(body) if body is not None else []
            note = None if facts else NO_FACTS
            accepted = await self._acceptances(cik) if facts else {}
            self.run.facts_added += self.repo.replace_facts(cik, facts, accepted)
            self.done[cik] = note
        self.run.with_facts += note is None
        self.run.no_facts += note is not None
        self.repo.note_facts(t, cik, self._at(), note)

    async def _acceptances(self, cik: int) -> dict[str, str]:
        """Acceptance times of its periodic reports, from the recent list and the older pages back to SINCE."""
        body = await self._json(edgar.submissions_url(cik))
        recent = body.get("filings", {}).get("recent", {}) if isinstance(body, dict) else {}
        out = xbrl.parse_acceptances(recent)
        for url in edgar.older_pages(body, SINCE):
            await self._turn(f"SEC filing times: CIK {cik}")
            out.update(xbrl.parse_acceptances(await self._json(url)))
        return out

    async def _splits(self, t: str) -> None:
        try:
            self.run.requests += 1
            splits = await YahooPrices(self.client).splits(Instrument(t, None), SINCE, self.r.clock.utcnow())
        except NoData:
            splits = []
        self.failed_in_a_row = 0
        self.repo.replace_splits(t, splits, self._at())
        self.run.splits += 1

    async def _json(self, url: str, missing_ok: bool = False) -> object:
        self.run.requests += 1
        resp = await self.client.get(SEC, url, headers=self.headers)
        if missing_ok and resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RequestFailed(SEC, resp.status_code, f"HTTP {resp.status_code}")
        self.failed_in_a_row = 0
        return resp.json()

    async def _guarded(self, t: str, step) -> None:
        """One member's step: an unexpected answer is that member's failure, never the run's end."""
        try:
            await step
        except (_Stopped, JobAlreadyRunning):
            raise
        except Exception as exc:
            self.run.failures.append(f"{t}: {type(exc).__name__}: {exc}")
            self.failed_in_a_row += 1
            if self.failed_in_a_row >= FAILED_IN_A_ROW:
                self.run.stopped = f"{FAILED_IN_A_ROW} requests failed in a row; run it again to resume"
                raise _Stopped from None

    async def _turn(self, label: str) -> None:
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


# The fundamental signals ------------------------------------------------------------------

SUE_NAME, GP_NAME, EY_NAME = "sue", "gross_profitability", "earnings_yield"
Timeline = tuple[list[date], list[Event]]


def _company(facts, accepted, splits) -> dict[str, list[Event]]:
    eps = xbrl.todays_shares(xbrl.quarters(xbrl.by_priority(facts, xbrl.EPS, accepted)), splits)
    gp = xbrl.trailing_year(xbrl.quarters(xbrl.gross_profit(facts, accepted)))
    assets = xbrl.instants(xbrl.by_priority(facts, xbrl.ASSETS, accepted))
    return {SUE_NAME: xbrl.timeline(xbrl.sue_events(eps)),
            GP_NAME: xbrl.timeline(xbrl.ratio_events(gp, assets)),
            EY_NAME: xbrl.timeline(xbrl.value_events(xbrl.trailing_year(eps)))}


def timelines(r: AppContext, tickers: Sequence[str], through: date | None = None) -> dict[str, dict[str, Timeline]]:
    """signal -> ticker -> (known days, events) of each ticker with stored facts; with `through`,
    only events known by then (a train-only run reads nothing later)."""
    repo = XbrlRepo(r.conn)
    members = repo.members()
    out: dict[str, dict[str, Timeline]] = {SUE_NAME: {}, GP_NAME: {}, EY_NAME: {}}
    cache: dict[int, tuple] = {}
    for t in tickers:
        m = members.get(t)
        if m is None or m.cik is None or m.facts_note is not None:
            continue
        if m.cik not in cache:
            cache[m.cik] = (repo.facts(m.cik), repo.accepted(m.cik))
        facts, accepted = cache[m.cik]
        for name, events in _company(facts, accepted, repo.splits(t)).items():
            events = [e for e in events if through is None or e.known <= through]
            if events:
                out[name][t] = ([e.known for e in events], events)
    return out


def _prepare(r: AppContext, p: Panel) -> None:
    if "xbrl" not in p.extra:
        tickers = sorted(set().union(*p.members)) if p.members else []
        p.extra["xbrl"] = timelines(r, tickers, p.days[-1] if p.days else None)


def _held(p: Panel, i: int, names: Sequence[str], name: str) -> dict[str, float]:
    """Each name's last event known before day i (the next session after its acceptance day),
    while it has a value and is under HOLD old."""
    day, lines = p.days[i], p.extra["xbrl"][name]
    out = {}
    for s in names:
        line = lines.get(s)
        if line is None:
            continue
        k = bisect_left(line[0], day) - 1
        if k >= 0 and (e := line[1][k]).value is not None and day - e.known <= xbrl.HOLD:
            out[s] = e.value
    return out


def sue(p: Panel, i: int, names: Sequence[str]) -> dict[str, float]:
    return _held(p, i, names, SUE_NAME)


def gross_profitability(p: Panel, i: int, names: Sequence[str]) -> dict[str, float]:
    return _held(p, i, names, GP_NAME)


def earnings_yield(p: Panel, i: int, names: Sequence[str]) -> dict[str, float]:
    """Trailing-year EPS in today's shares over the last close in today's shares (Yahoo's, split-adjusted)."""
    out = {}
    for s, eps in _held(p, i, names, EY_NAME).items():
        c = p.split_close[s][i - 1] if s in p.split_close and i >= 1 else float("nan")
        if c > 0:
            out[s] = eps / c
    return out


SUE = Signal(SUE_NAME, "SUE: quarterly EPS minus the same quarter a year before, over the spread of the 8 changes "
                       "before it; held from the day after its filing", (60,), sue, prepare=_prepare,
             keep_priced_duplicate=True)
GROSS_PROFITABILITY = Signal(GP_NAME, "Gross profitability: trailing-year gross profit over total assets at the "
                                      "quarter's end", (20,), gross_profitability, prepare=_prepare,
                             keep_priced_duplicate=True)
EARNINGS_YIELD = Signal(EY_NAME, "Earnings yield: trailing-year diluted EPS over the last close", (20,),
                        earnings_yield, prepare=_prepare, keep_priced_duplicate=True)
FUNDAMENTAL_SIGNALS = (SUE, GROSS_PROFITABILITY, EARNINGS_YIELD)


# Coverage ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class XbrlYear:
    year: int
    members: int
    """Priced members that were members on some day of the year."""
    with_cik: int
    with_facts: int
    scored: dict[str, int]
    """signal -> members with a scored filing known that year."""
    events: dict[str, int]
    """signal -> scored filings known that year."""


@dataclass(frozen=True)
class XbrlCoverage:
    years: tuple[XbrlYear, ...]
    priced: int
    with_cik: int
    with_facts: int
    without_cik: tuple[str, ...]
    without_facts: tuple[str, ...]
    last_run: XbrlRun | None
    tickers_fetched_at: str | None


def fundamentals_coverage(ctx: AppContext, start: date = START) -> XbrlCoverage:
    """Per year: the priced members, those with a CIK, with facts, and per signal the members with a
    scored filing that year and how many; plus who has no CIK or no facts."""
    with research_context(ctx) as r:
        repo, tb = XbrlRepo(r.conn), TestbedRepo(r.conn)
        priced = set(_priced(r))
        members = repo.members()
        lines = timelines(r, sorted(priced))
        years: dict[int, set[str]] = {}
        today = r.clock.utcnow().date()
        for s in tb.spans():
            if s.ticker not in priced:
                continue
            for y in range(max(s.start, start).year, (s.end or today).year + 1):
                years.setdefault(y, set()).add(s.ticker)
        has_cik = {t for t in priced if (m := members.get(t)) and m.cik is not None}
        has_facts = {t for t in has_cik if members[t].facts_note is None and members[t].facts_tried_at}
        rows = []
        for y, ts in sorted(years.items()):
            scored, events = {}, {}
            for name, by in lines.items():
                hits = {t: sum(e.known.year == y and e.value is not None for e in by[t][1]) for t in ts if t in by}
                scored[name] = sum(1 for n in hits.values() if n)
                events[name] = sum(hits.values())
            rows.append(XbrlYear(y, len(ts), len(ts & has_cik), len(ts & has_facts), scored, events))
        return XbrlCoverage(tuple(rows), len(priced), len(has_cik), len(has_facts),
                            tuple(sorted(t for t in priced if members.get(t) and members[t].facts_note == NO_CIK)),
                            tuple(sorted(t for t in priced if members.get(t) and members[t].facts_note == NO_FACTS)),
                            _last_run(r), repo.tickers_fetched_at())
