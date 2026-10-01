"""Form 4 insider trades and typed SEC filings of the testbed's members (next-signals 05), point
in time, in the research database: each issuer's submissions (Form 4 and 8-K acceptance times,
8-K items) and SEC's quarterly Insider Transactions Data Sets (open-market purchases and sales).
Resumable (an issuer or quarter fetched is not asked again) and it steps aside for live work.

Then the `insider_buys` signal: distinct insiders buying in the 90 days before the decision day."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from oracle.analysts.signal_test import Panel, Signal
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research import edgar, insider
from oracle.research.insider import Buys
from oracle.services.context import AppContext
from oracle.services.http import open_client
from oracle.services.jobs_service import single_flight
from oracle.services.testbed_service import START, research_context
from oracle.services.xbrl_service import SEC, SINCE, SecFetch, lookup_cik, priced_members, FetchStopped
from oracle.store.db import to_utc_iso
from oracle.store.insider import InsiderRepo
from oracle.store.meta import MetaRepo
from oracle.store.testbed import TestbedRepo
from oracle.store.xbrl import XbrlRepo

__all__ = ["INSIDER_BUYS", "InsiderCoverage", "InsiderRun", "JOB", "LateCik", "fetch_insiders", "insider_coverage",
           "issuer_data", "last_insider_run"]

JOB = "insider_fetch"
LEASE_S = 1800.0
LAST_RUN = "insider_last_run"
FIRST_QUARTER = "2009q1"
"""A 90-day window and SUE-length history before the testbed's first day."""


@dataclass
class InsiderRun:
    members: int = 0
    issuers: int = 0
    """Distinct CIKs of the priced members in scope."""
    no_cik: int = 0
    requests: int = 0
    filings_fetched: int = 0
    filings_added: int = 0
    quarters_fetched: int = 0
    trades_added: int = 0
    stopped: str | None = None
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0
    finished_at: str | None = None

    @property
    def summary(self) -> str:
        head = f"stopped early ({self.stopped})" if self.stopped else "finished"
        tail = f"; {len(self.failures)} failed, first: {self.failures[0]}" if self.failures else ""
        return (f"{head}: {self.members} members ({self.issuers} issuers, {self.no_cik} without a CIK), "
                f"{self.requests} requests, {self.filings_fetched} issuers' filings ({self.filings_added} Form 4s "
                f"and 8-Ks), {self.quarters_fetched} quarters of trades ({self.trades_added} trades){tail}")


async def fetch_insiders(ctx: AppContext, *, refresh: bool = False, only: Sequence[str] | None = None,
                         limit: int | None = None, stop: Callable[[], bool] | None = None,
                         busy: Callable[[], str | None] | None = None,
                         on_progress: Callable[[str], None] = lambda _: None) -> InsiderRun:
    """Fetch what the research database lacks: each priced member's issuer filings, then every
    published quarter's trades of those issuers. With `refresh`, every issuer's filings and every
    quarter again (a quarter is filtered to the issuers known when fetched). `only` / `limit`
    narrow the members (a smoke run); `busy` names live work to wait for; `stop` ends the run."""
    began = time.monotonic()
    run = InsiderRun()
    with research_context(ctx) as r:
        try:
            with single_flight(r, JOB, LEASE_S) as held:
                f = _InsiderFetch(r, held, run, stop, busy, on_progress)
                try:
                    async with open_client(r) as client:
                        f.client = client
                        await f.ticker_map(False)
                        ciks = f.issuers(f.scope(only, limit))
                        await f.filings(ciks, refresh)
                        await f.quarters(ciks, refresh)
                except FetchStopped:
                    pass
        except Exception as exc:
            run.stopped = f"failed: {type(exc).__name__}: {exc}"
            raise
        finally:
            run.seconds = round(time.monotonic() - began, 1)
            run.finished_at = to_utc_iso(ctx.clock.utcnow())
            MetaRepo(r.conn).set(LAST_RUN, json.dumps(asdict(run)))
    return run


def last_insider_run(ctx: AppContext) -> InsiderRun | None:
    with research_context(ctx) as r:
        return _last_run(r)


def _last_run(r: AppContext) -> InsiderRun | None:
    raw = MetaRepo(r.conn).get(LAST_RUN)
    return InsiderRun(**json.loads(raw)) if raw else None


class _InsiderFetch(SecFetch):
    """The fundamentals fetch's pacing, waits, contact and failure rules, for Form 4s and 8-Ks."""

    def __init__(self, r, held, run, stop, busy, on_progress) -> None:
        super().__init__(r, held, run, stop, busy, on_progress)
        self.insiders = InsiderRepo(r.conn)

    def issuers(self, tickers: list[str]) -> list[int]:
        ciks = set()
        for t in tickers:
            cik = lookup_cik(self.repo, t)
            self.run.no_cik += cik is None
            if cik is not None:
                ciks.add(cik)
        self.run.issuers = len(ciks)
        return sorted(ciks)

    async def filings(self, ciks: list[int], refresh: bool) -> None:
        tried = self.insiders.filings_tried()
        for n, cik in enumerate(ciks, 1):
            if refresh or cik not in tried:
                await self._turn(f"SEC filings: CIK {cik} ({n}/{len(ciks)})")
                await self._guarded(f"CIK {cik}", self._issuer(cik))

    async def _issuer(self, cik: int) -> None:
        body = await self._json(edgar.submissions_url(cik), missing_ok=True)
        recent = body.get("filings", {}).get("recent", {}) if isinstance(body, dict) else {}
        found = insider.parse_filings(recent, cik)
        for url in edgar.older_pages(body, SINCE):
            await self._turn(f"SEC filings: CIK {cik}, older page")
            found += insider.parse_filings(await self._json(url), cik)
        self.run.filings_added += self.insiders.replace_filings(cik, found, self._at())
        self.run.filings_fetched += 1

    async def quarters(self, ciks: list[int], refresh: bool) -> None:
        await self._turn("reading SEC's insider data sets page")
        try:
            links = insider.dataset_links(await self._text(insider.DATASETS_PAGE))
        except (RequestFailed, CircuitOpen) as exc:
            self.run.failures.append(f"data sets page: {exc}")
            return
        stored = self.insiders.quarter_issuers()
        scope = frozenset(ciks)
        # A quarter is done only for the issuers it was filtered to; a refetch keeps those too, so a
        # smoke run never shrinks what a full one stored.
        wanted = [q for q in sorted(links)
                  if q >= FIRST_QUARTER and (refresh or not scope <= stored.get(q, frozenset()))]
        for n, q in enumerate(wanted, 1):
            await self._turn(f"insider trades {q} ({n}/{len(wanted)})")
            await self._guarded(q, self._quarter(q, links[q], scope | stored.get(q, frozenset())))

    async def _quarter(self, q: str, url: str, ciks: frozenset[int]) -> None:
        self.run.requests += 1
        resp = await self.client.get(SEC, url, headers=self.headers)
        if resp.status_code != 200:
            raise RequestFailed(SEC, resp.status_code, f"HTTP {resp.status_code}")
        self.failed_in_a_row = 0
        trades = await asyncio.to_thread(insider.parse_dataset, resp.content, ciks)
        self.insiders.replace_quarter(q, url, trades, ciks, self._at())
        self.run.quarters_fetched += 1
        self.run.trades_added += len(trades)

    async def _text(self, url: str) -> str:
        self.run.requests += 1
        resp = await self.client.get(SEC, url, headers=self.headers)
        if resp.status_code != 200:
            raise RequestFailed(SEC, resp.status_code, f"HTTP {resp.status_code}")
        return resp.text


# What the signal and the event study read -------------------------------------------------

@dataclass(frozen=True)
class IssuerData:
    buys: Buys
    items: dict[str, list[date]]
    """8-K item -> known days."""
    first_form4: date | None
    """Known day of the CIK's first stored Form 4: before it this CIK says nothing about insiders
    (a company under a CIK it took later, or a ticker since given to another company)."""
    quarters: frozenset[str]
    """The data-set quarters fetched with this CIK in scope."""

    def covers(self, first: date, last: date) -> bool:
        """Whether its insider data is whole from `first` to `last`."""
        if self.first_form4 is None or first < self.first_form4:
            return False
        q, end = insider.quarter_of(first), insider.quarter_of(last)
        while q <= end:
            if q not in self.quarters:
                return False
            q = insider.quarter_of(insider.quarter_end(q) + timedelta(days=1))
        return True

    @property
    def scored_from(self) -> date | None:
        """The first decision day its window could be whole (quarters aside)."""
        return None if self.first_form4 is None else self.first_form4 + insider.WINDOW


@dataclass(frozen=True)
class InsiderData:
    by_ticker: dict[str, IssuerData]
    """Members with a CIK."""
    through: date | None
    """The last day of the latest stored quarter of trades: no insider data after it."""


def issuer_data(r: AppContext, tickers: Sequence[str], through: date | None = None) -> InsiderData:
    """Each ticker with a CIK: its insider purchases and 8-K item days by known day; with `through`,
    only what was known by then (a train-only run reads nothing later)."""
    xr, ir = XbrlRepo(r.conn), InsiderRepo(r.conn)
    quarters = ir.quarter_issuers()
    last = insider.quarter_end(max(quarters)) if quarters else None
    cache: dict[int, IssuerData] = {}
    out = {}
    for t in tickers:
        cik = lookup_cik(xr, t)
        if cik is None:
            continue
        if cik not in cache:
            b = insider.buys(ir.trades(cik), ir.accepted(cik))
            filings = ir.filings(cik)
            items = insider.item_events(filings)
            first = min((insider.known_day(f.filed, f.accepted) for f in filings if f.form == insider.FORM4),
                        default=None)
            if through is not None:
                k = sum(d <= through for d in b[0])
                b = (b[0][:k], b[1][:k])
                items = {i: [d for d in days if d <= through] for i, days in items.items()}
            cache[cik] = IssuerData(b, items, first if through is None or (first and first <= through) else None,
                                    frozenset(q for q, ciks in quarters.items() if cik in ciks))
        out[t] = cache[cik]
    return InsiderData(out, last)


def _prepare(r: AppContext, p: Panel) -> None:
    if "insider" not in p.extra:
        tickers = sorted(set().union(*p.members)) if p.members else []
        p.extra["insider"] = issuer_data(r, tickers, p.days[-1] if p.days else None)


def insider_buys(p: Panel, i: int, names: Sequence[str]) -> dict[str, float]:
    """Distinct insiders buying in Form 4s known from 90 days before the decision day to the day
    before. A member scores (0 without buys) only when its CIK's insider data covers that window: its
    first Form 4 is no later than the window's start and every quarter in it was fetched with the CIK.
    Nothing after the trades' last quarter."""
    data: InsiderData = p.extra["insider"]
    day = p.days[i]
    if data.through is None or day > data.through:
        return {}
    first, last = day - insider.WINDOW, day - timedelta(days=1)
    return {s: float(insider.insider_score(d.buys, day)) for s in names
            if (d := data.by_ticker.get(s)) and d.covers(first, last)}


INSIDER_BUYS = Signal("insider_buys", "Insider buys: distinct officers and directors with an open-market purchase in "
                                      "Form 4s known in the 90 days before", (20, 60), insider_buys, prepare=_prepare,
                      keep_priced_duplicate=True)


# Coverage ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class InsiderCoverage:
    priced: int
    issuers: int
    no_cik: int
    filings_fetched: int
    quarters: dict[str, int]
    """quarter -> trades stored."""
    years: dict[int, dict[str, int]]
    """year -> purchases, sales, issuers with each, Form 4s, 8-Ks and each item's 8-Ks."""
    last_run: InsiderRun | None
    late: tuple[LateCik, ...] = ()
    """Priced members whose CIK's Form 4s start over LATE after they were first a member from START."""


LATE = timedelta(days=365)


@dataclass(frozen=True)
class LateCik:
    ticker: str
    cik: int
    member_from: date
    scored_from: date | None
    """None: no Form 4 under today's CIK at all."""


def insider_coverage(ctx: AppContext) -> InsiderCoverage:
    with research_context(ctx) as r:
        xr, ir = XbrlRepo(r.conn), InsiderRepo(r.conn)
        priced = priced_members(r)
        ciks = {c for t in priced if (c := lookup_cik(xr, t)) is not None}
        years: dict[int, dict[str, int]] = {}
        for y, row in ir.counts_by_year().items():
            years.setdefault(y, {}).update(row)
        for y, row in ir.items_by_year().items():
            years.setdefault(y, {}).update(row)
        data = issuer_data(r, priced)
        starts: dict[str, date] = {}
        for span in TestbedRepo(r.conn).spans():
            if span.ticker in data.by_ticker and (span.end is None or span.end > START):
                starts.setdefault(span.ticker, max(span.start, START))
        late = tuple(LateCik(t, lookup_cik(xr, t), d, f) for t, d in sorted(starts.items())
                     if (f := data.by_ticker[t].scored_from) is None or f > d + LATE)
        return InsiderCoverage(len(priced), len(ciks), sum(lookup_cik(xr, t) is None for t in priced),
                               len(set(ir.filings_tried()) & ciks), ir.quarters(), dict(sorted(years.items())),
                               _last_run(r), late)
