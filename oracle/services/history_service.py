"""The history backfill: for the main list and each one's industry map, daily price bars from
2021-09, SEC 8-K / 10-Q / 10-K filings (US names) and Google News date-window searches from
2022-09, each article kept under its real published date. Resumable (finished windows are
stored and skipped), paced through the shared gate, and it steps aside for live work. Also the
coverage it reached, per company and quarter."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research.news import GOOGLE_NEWS, FeedUnreadable, google_news_params, parse_google_news
from oracle.research.queries import short_name
from oracle.research.tools import ToolError
from oracle.services.context import AppContext
from oracle.services.errors import PriceUnavailable, UnknownSymbol
from oracle.services.http import open_client
from oracle.services.industry_service import industry_map
from oracle.services.jobs_service import Held, single_flight
from oracle.services.never_list import never_company
from oracle.services.prices_service import backfill_listing
from oracle.services.research_tools.filings import NoFilings, filing_article, filings_since, submissions
from oracle.services.research_tools.shelf import Unavailable
from oracle.services.watch_service import DEFAULT_LIST, watchlist_symbols
from oracle.store.articles import ArticleRepo
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.history import HistoryRepo, Window
from oracle.store.sec import SecTickerRepo
from oracle.store.universe import UniverseRepo

__all__ = ["FILING_FORMS", "GOOGLE_CAP", "NEWS_START", "PRICE_START", "STAGES", "THIN", "Cell", "Company",
           "Coverage", "CoverageRow", "HistoryRun", "backfill_history", "company_history", "coverage",
           "history_companies", "news_query", "news_windows", "search_name"]

NEWS_START = date(2022, 9, 1)
PRICE_START = date(2021, 9, 1)
PRICE_SLACK = timedelta(days=30)
"""A first bar within PRICE_START's month already covers it (the stored main list starts 2021-09-21:
five years back from when it was first priced)."""
GOOGLE_CAP = 100
"""Google News gives at most this many results a search: a quarter that fills it is asked
again month by month."""
FILING_FORMS = frozenset({"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"})
THIN = 5
"""News articles in a quarter below which the coverage report calls it thin."""
NEWS, FILINGS, PRICES = "google_news", "sec_edgar", "prices"
STAGES = ("prices", "filings", "news")
"""Bars first (06 needs them and they are quick), then filings, then the long news searches."""
JOB = "history_backfill"
LEASE_S = 1800.0
WAIT_STEP_S = 5.0
LOCKOUT_WAITS = 1
"""Times a run waits out a source's open breaker before it stops; a restart resumes."""
FAILED_IN_A_ROW = 5
"""Failed searches in a row after which a stage stops rather than keep asking."""


@dataclass(frozen=True)
class Company:
    symbol: str
    """The primary listing: history is kept under it."""
    name: str
    main: bool
    """On the main list itself, not only on a map."""
    of: tuple[str, ...]
    """The main-list companies whose map it is on."""


@dataclass
class HistoryRun:
    companies: int = 0
    requests: int = 0
    kept: int = 0
    """News articles and filings stored for a company (an article kept for two counts twice)."""
    undated: int = 0
    outside: int = 0
    prices_added: int = 0
    windows: int = 0
    """Windows finished by this run."""
    stopped: str | None = None
    """Why it ended early: "cancelled", or a source that kept refusing."""
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0


def history_companies(ctx: AppContext, watchlist: str = DEFAULT_LIST) -> list[Company]:
    """The list's companies, then every company on their industry maps, each once, by primary
    listing; never a never-listed company (Tesla) and never a fund."""
    universe = UniverseRepo(ctx.conn)
    out: dict[str, Company] = {}

    def add(symbol: str, main: bool, of: str | None) -> str | None:
        p = universe.profile(symbol) if universe.resolve(symbol) else None
        if p is None or p.type.lower() != "equity" or never_company(ctx, symbol, user=True):
            return None
        key = p.primary_symbol
        have = out.get(key.casefold())
        if have is None:
            out[key.casefold()] = Company(key, p.name, main, (of,) if of else ())
        elif of and of not in have.of and not have.main:
            out[key.casefold()] = Company(key, have.name, have.main, (*have.of, of))
        return key

    mains = [s for s in (add(x, True, None) for x in watchlist_symbols(ctx, watchlist)[1]) if s]
    for symbol in mains:
        for r in industry_map(ctx, symbol).related:
            add(r.symbol, False, symbol)
    return list(out.values())


def news_windows(start: date, today: date) -> list[tuple[date, date]]:
    """[start, end) windows: calendar quarters from `start`, cut at the first of `today`'s month,
    so every window is over; an unfinished month is live research's."""
    stop = today.replace(day=1)
    out, s = [], start
    while s < stop:
        q = _quarter_start(s)
        end = min(_add_months(q, 3), stop)
        out.append((s, end))
        s = end
    return out


def news_query(name: str, start: date, end: date) -> str:
    """The company's everyday name in quotes, inside Google's date operators. The answer is
    still checked against the window: Google's edges drift by the outlet's timezone."""
    return f'"{search_name(name)}" after:{start.isoformat()} before:{end.isoformat()}'


# Share-class and listing words, and long legal forms short_name keeps, that no headline uses.
_LISTING_WORDS = re.compile(r"[\s,]+(?:class [a-z]\b|common stock|ordinary shares|sponsored adr|adr|ads|gdr|"
                            r"aktiengesellschaft|societe europeenne)\b.*$", re.IGNORECASE)
MIN_SEARCH_NAME = 3
"""A shorter name ("BT") matches every headline with those letters: the fuller one is searched."""


def search_name(name: str) -> str:
    """The company's name as headlines write it: no share class, legal form, leading "The" or
    words after a dash."""
    base = name.split(" - ", 1)[0].strip()
    base = re.sub(r"^the\s+", "", _LISTING_WORDS.sub("", base), flags=re.IGNORECASE).strip(" ,")
    short = short_name(base)
    return short if len(short) >= MIN_SEARCH_NAME else base


async def backfill_history(ctx: AppContext, *, watchlist: str = DEFAULT_LIST, only: Sequence[str] | None = None,
                           start: date = NEWS_START, stages: Sequence[str] = STAGES,
                           stop: Callable[[], bool] | None = None, busy: Callable[[], str | None] | None = None,
                           on_progress: Callable[[str], None] = lambda _: None) -> HistoryRun:
    """Everything not yet stored, company by company (main list first). `only` limits it to
    those companies; `busy` names live work to wait for between requests; `stop` ends it there."""
    began = time.monotonic()
    companies = history_companies(ctx, watchlist)
    if only is not None:
        wanted = {s.casefold() for s in only}
        companies = [c for c in companies if c.symbol.casefold() in wanted]
    with single_flight(ctx, JOB, LEASE_S) as held:
        run = _Run(ctx, held, companies, start, stop, busy, on_progress)
        try:
            async with open_client(ctx) as client:
                counted = _Counting(client, run.result)
                for stage in STAGES:
                    if stage in stages and not run.result.stopped:
                        await getattr(run, stage)(counted)
        except _Stopped:
            pass
    run.result.seconds = round(time.monotonic() - began, 1)
    return run.result


def company_history(ctx: AppContext, symbol: str) -> list[int]:
    """The articles and filings the backfill kept for a company (its primary listing), oldest
    first: what 06 scores for it."""
    universe = UniverseRepo(ctx.conn)
    p = universe.profile(symbol) if universe.resolve(symbol) else None
    return HistoryRepo(ctx.conn).article_ids(p.primary_symbol if p else symbol)


@dataclass(frozen=True)
class Cell:
    news: int
    filings: int
    outlets: int


@dataclass(frozen=True)
class CoverageRow:
    symbol: str
    name: str
    main: bool
    of: tuple[str, ...]
    cells: dict[str, Cell]
    """Quarter -> counts; a quarter with nothing stored is absent."""
    thin: tuple[str, ...]
    """Quarters with fewer than THIN news articles."""
    first_bar: date | None


@dataclass(frozen=True)
class Coverage:
    start: date
    quarters: tuple[str, ...]
    rows: tuple[CoverageRow, ...]
    windows_done: int
    """News windows finished (month re-asks of a full quarter included)."""
    windows_left: int
    """Quarter windows not yet asked, and filings and prices not yet tried."""
    caveat: str = ("today's list and maps taken back in time (survivorship); undated results are dropped, "
                   "not dated by the day fetched")


def coverage(ctx: AppContext, watchlist: str = DEFAULT_LIST, start: date = NEWS_START) -> Coverage:
    """What the backfill has stored per company and quarter, and how much is left, reading only."""
    companies = history_companies(ctx, watchlist)
    repo = HistoryRepo(ctx.conn)
    today = ctx.clock.utcnow().date()
    windows = news_windows(start, today)
    quarters = tuple(dict.fromkeys(_quarter(s) for s, _ in windows))
    counts: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for q in repo.quarters([c.symbol for c in companies]):
        counts.setdefault((q.symbol.casefold(), q.quarter), {})[q.source] = (q.articles, q.outlets)
    done = {src: repo.done(src) for src in (PRICES, FILINGS, NEWS)}
    bars = BarRepo(ctx.conn)
    rows, left = [], 0
    for c in companies:
        key = c.symbol.casefold()
        cells = {}
        for quarter in quarters:
            found = counts.get((key, quarter), {})
            news, filings = found.get(NEWS, (0, 0)), found.get(FILINGS, (0, 0))
            if news[0] or filings[0]:
                cells[quarter] = Cell(news[0], filings[0], news[1] + filings[1])
        thin = tuple(q for q in quarters if (cells[q].news if q in cells else 0) < THIN)
        left += sum((key, s, e) not in done[NEWS] for s, e in windows)
        left += not any(k[0] == key for k in done[FILINGS]) and _us(c.symbol)
        first = (span := bars.span([c.symbol])) and span[0]
        left += not _has_prices(first) and not any(k[0] == key for k in done[PRICES])
        rows.append(CoverageRow(c.symbol, c.name, c.main, c.of, cells, thin, first))
    return Coverage(start, quarters, tuple(rows), len(done[NEWS]), left)


class _Stopped(Exception):
    pass


class _Run:
    def __init__(self, ctx: AppContext, held: Held, companies: list[Company], start: date, stop, busy,
                 on_progress) -> None:
        self.ctx, self.held, self.companies, self.start = ctx, held, companies, start
        self.stop, self.busy, self.on_progress = stop, busy, on_progress
        self.repo, self.articles = HistoryRepo(ctx.conn), ArticleRepo(ctx.conn)
        self.today = ctx.clock.utcnow().date()
        self.result = HistoryRun(companies=len(companies))
        self.lockouts = 0

    async def prices(self, client: RateLimitedClient) -> None:
        done = self.repo.done(PRICES)
        bars = BarRepo(self.ctx.conn)
        tried = {k[0] for k in done}
        todo = [c for c in self.companies if c.symbol.casefold() not in tried
                and not _has_prices((span := bars.span([c.symbol])) and span[0])]
        for i, c in enumerate(todo, 1):
            await self._turn(f"price bars from {PRICE_START}: {c.symbol} ({i}/{len(todo)})")
            try:
                r = await backfill_listing(self.ctx, client, c.symbol, PRICE_START)
            except PriceUnavailable as exc:
                if not exc.no_data:
                    self.result.failures.append(f"{c.symbol} prices: {exc}")
                    continue
                self._finish(Window(c.symbol, PRICES, PRICE_START, self.today, 0, 0, 0, 0, str(exc)), [])
                continue
            except UnknownSymbol as exc:
                self.result.failures.append(f"{c.symbol} prices: {exc}")
                continue
            self.result.prices_added += r.added
            self._finish(Window(c.symbol, PRICES, PRICE_START, self.today, r.added, r.added, 0, 0, None), [])

    async def filings(self, client: RateLimitedClient) -> None:
        contact = self.ctx.config.research.contact
        if not contact:
            self.result.failures.append('filings skipped: SEC needs [research] contact = "Name email"')
            return
        done = {k[0] for k in self.repo.done(FILINGS)}
        todo = [c for c in self.companies if _us(c.symbol) and c.symbol.casefold() not in done]
        tickers = SecTickerRepo(self.ctx.conn)
        for i, c in enumerate(todo, 1):
            await self._turn(f"SEC filings: {c.symbol} ({i}/{len(todo)})")
            try:
                cik, body = await submissions(client, tickers, self.ctx.clock, contact, c.symbol)
                filings = await filings_since(client, contact, body, cik, self.start)
            except NoFilings as exc:
                self._finish(Window(c.symbol, FILINGS, self.start, self.today, 0, 0, 0, 0, str(exc)), [])
                continue
            except ToolError as exc:
                if isinstance(exc, Unavailable) and isinstance(exc.__cause__, CircuitOpen):
                    await self._locked_out(exc.__cause__, FILINGS)
                else:
                    self.result.failures.append(f"{c.symbol} filings: {exc}")
                continue
            wanted = [f for f in filings if f.form in FILING_FORMS]
            kept = [f for f in wanted if self.start <= f.filed < self.today]
            ids = self.articles.save([filing_article(f) for f in kept], FILINGS, self._now(), c.symbol.upper())
            self.result.kept += len(ids)
            self.result.outside += len(wanted) - len(kept)
            self._finish(Window(c.symbol, FILINGS, self.start, self.today, len(wanted), len(ids), 0,
                                len(wanted) - len(kept)), ids)

    async def news(self, client: RateLimitedClient) -> None:
        done = self.repo.done(NEWS)
        windows = news_windows(self.start, self.today)
        plan = [(c, s, e) for c in self.companies for s, e in windows]
        failed = 0
        for i, (c, s, e) in enumerate(plan, 1):
            months = []
            label = f"news {c.symbol} {_quarter(s)} ({i}/{len(plan)} windows)"
            w = done.get((c.symbol.casefold(), s, e))
            if w is None:
                w = await self._search(client, c, s, e, label)
                if w is None:
                    failed += 1
                    if failed >= FAILED_IN_A_ROW:
                        self.result.stopped = f"{NEWS}: {FAILED_IN_A_ROW} searches failed in a row"
                        return
                    continue
                failed = 0
            if w.found >= GOOGLE_CAP:
                months = [(m, _add_months(m, 1)) for m in _months(s, e)]
            for ms, me in months:
                if (c.symbol.casefold(), ms, me) not in done:
                    await self._search(client, c, ms, me, f"{label}, month {ms:%Y-%m}")

    async def _search(self, client: RateLimitedClient, c: Company, s: date, e: date, label: str) -> Window | None:
        await self._turn(label)
        q = news_query(c.name, s, e)
        try:
            resp = await client.get(NEWS, GOOGLE_NEWS, params=google_news_params(q))
            if resp.status_code != 200:
                raise RequestFailed(NEWS, resp.status_code, f"HTTP {resp.status_code}")
            found = parse_google_news(resp.text)
        except CircuitOpen as exc:
            await self._locked_out(exc, NEWS)
            # Waited out: the same window once more (a second lockout stops the run).
            return await self._search(client, c, s, e, label)
        except (RequestFailed, FeedUnreadable) as exc:
            self.result.failures.append(f"{c.symbol} {s}..{e}: {exc}")
            return None
        start, end = _utc(s), _utc(e)
        dated = [a for a in found if a.published is not None]
        inside = [a for a in dated if start <= a.published < end]
        ids = self.articles.save(inside, NEWS, self._now(), q)
        w = Window(c.symbol, NEWS, s, e, len(found), len(ids), len(found) - len(dated), len(dated) - len(inside))
        self.result.kept += w.kept
        self.result.undated += w.undated
        self.result.outside += w.outside
        self._finish(w, ids)
        return w

    async def _locked_out(self, exc: CircuitOpen, source: str) -> None:
        """A source's breaker is open: wait it out once, then stop (a restart resumes)."""
        self.lockouts += 1
        if self.lockouts > LOCKOUT_WAITS:
            self.result.stopped = f"{source} refused: {exc}"
            raise _Stopped
        wait = max(0.0, exc.retry_at - self.ctx.clock.utcnow().timestamp())
        self.on_progress(f"{source} is resting; waiting {wait:.0f} s")
        await self._sleep(wait)

    async def _turn(self, label: str) -> None:
        """Between requests: stop when told, and wait while live work runs."""
        self._check()
        while self.busy and (other := self.busy()):
            self.on_progress(f"waiting for {other}")
            await self.ctx.clock.sleep(WAIT_STEP_S)
            self._check()
        self.on_progress(label)

    async def _sleep(self, seconds: float) -> None:
        while seconds > 0:
            self._check()
            step = min(WAIT_STEP_S, seconds)
            await self.ctx.clock.sleep(step)
            seconds -= step

    def _check(self) -> None:
        self.held.check()
        if self.stop and self.stop():
            self.result.stopped = "cancelled"
            raise _Stopped

    def _finish(self, w: Window, ids: list[int]) -> None:
        self.repo.finish(w, ids, self._now())
        self.result.windows += 1

    def _now(self) -> str:
        return to_utc_iso(self.ctx.clock.utcnow())


class _Counting:
    """The client, counting the requests asked of it (retries inside it not counted)."""

    def __init__(self, client: RateLimitedClient, result: HistoryRun) -> None:
        self._client, self._result = client, result

    async def get(self, *args, **kwargs):
        self._result.requests += 1
        return await self._client.get(*args, **kwargs)

    async def post(self, *args, **kwargs):
        self._result.requests += 1
        return await self._client.post(*args, **kwargs)


def _has_prices(first: date | None) -> bool:
    return first is not None and first <= PRICE_START + PRICE_SLACK


def _us(symbol: str) -> bool:
    """Yahoo marks non-US listings with an exchange suffix (VOD.L, 7203.T)."""
    return "." not in symbol


def _utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _quarter(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _months(s: date, e: date) -> list[date]:
    out, m = [], s.replace(day=1)
    while m < e:
        out.append(m)
        m = _add_months(m, 1)
    return out
