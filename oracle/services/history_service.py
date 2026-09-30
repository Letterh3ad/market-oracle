"""The history backfill: for the main list and each one's industry map, daily price bars from
2021-09, SEC 8-K / 10-Q / 10-K filings (US names) and Google News date-window searches from
2022-09, each article kept under its real published date. Resumable (finished windows are
stored and skipped), paced through the shared gate, and it steps aside for live work. Also the
coverage it reached, per company and quarter, with how much of each company's news names it."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.net.gate import SourceGate
from oracle.research.news import GOOGLE_NEWS, FeedUnreadable, google_news_params, parse_google_news
from oracle.research.tools import ToolError
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, JobAlreadyRunning, PriceUnavailable, UnknownSymbol
from oracle.services.history_names import SearchName, default_search, is_listing_code, mentions, seeded
from oracle.services.http import open_client
from oracle.services.industry_service import industry_map
from oracle.services.jobs_service import Held, job_statuses, single_flight
from oracle.services.never_list import never_company
from oracle.services.prices_service import backfill_listing, price_sources
from oracle.services.research_tools.filings import NoFilings, filing_article, filings_since, submissions
from oracle.services.research_tools.shelf import Unavailable
from oracle.services.universe_service import resolve_symbol
from oracle.services.watch_service import DEFAULT_LIST, watchlist_symbols
from oracle.store.articles import ArticleRepo
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.history import HistoryRepo, Window
from oracle.store.meta import MetaRepo
from oracle.store.sec import SecTickerRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import is_exchange_product, is_receipt, is_receipt_symbol

__all__ = ["FILING_FORMS", "GOOGLE_CAP", "NEWS_START", "NOISY", "PRICE_START", "STAGES", "THIN", "Cell", "Company",
           "Coverage", "CoverageRow", "HistoryRun", "backfill_history", "clear_search_name", "company_history",
           "coverage", "history_companies", "last_run", "news_query", "news_windows", "search_for",
           "set_search_name"]

NEWS_START = date(2022, 9, 1)
PRICE_START = date(2021, 9, 1)
PRICE_SLACK = timedelta(days=30)
"""A first bar within PRICE_START's month already covers it (the stored main list starts 2021-09-21:
five years back from when it was first priced)."""
GOOGLE_CAP = 100
"""Google News gives at most this many results a search: a main-list company's quarter that fills
it is asked again month by month."""
FILING_FORMS = frozenset({"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"})
THIN = 5
"""News articles in a quarter below which the coverage report calls it thin."""
NOISY = 0.5
"""Share of a quarter's headlines naming the company below which the report calls it noisy."""
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
LAST_RUN = "history_last_run"
"""Meta key: the last run's result, shown on the coverage card."""
PLAN_TTL_S = 600.0
"""How long the companies of a list (its maps read, 15 s on the live list) are reused."""
# Warrants and preferred shares: a line of a company, never a company.
_NOT_SHARES_SYMBOL = re.compile(r"-(?:WT|W|UN|P[A-Z]?)(?:\.|$)", re.IGNORECASE)
_NOT_SHARES_NAME = re.compile(r"\b(?:warrants?|preferred|pref(?:erence)?\s+shares?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Company:
    symbol: str
    """The primary listing: history is kept under it."""
    name: str
    main: bool
    """On the main list itself, not only on a map."""
    of: tuple[str, ...]
    """The main-list companies whose map it is on."""
    search: SearchName = field(default_factory=lambda: SearchName("", ()))


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
    finished_at: str | None = None

    @property
    def summary(self) -> str:
        """One line for Activity and the card."""
        head = f"stopped early ({self.stopped})" if self.stopped else "finished"
        tail = f"; {len(self.failures)} failed, first: {self.failures[0]}" if self.failures else ""
        return (f"{head}: {self.requests} requests, {self.windows} windows, {self.kept} kept, "
                f"{self.prices_added} price bars{tail}")


def search_for(ctx: AppContext, symbol: str, name: str) -> SearchName:
    """The user's name for the company, else one set by hand, else its universe name cleaned."""
    own = HistoryRepo(ctx.conn).names().get(symbol.casefold())
    if own:
        return SearchName(*own, source="user")
    return seeded(symbol) or default_search(name)


def set_search_name(ctx: AppContext, symbol: str, query: str, aliases: Sequence[str] = (),
                    skip: bool = False) -> tuple[str, int]:
    """Search the company's news by `query` (a quoted phrase, several joined by OR, context words)
    or with `skip` not at all; the headlines naming one of `aliases` count as on topic. Its news
    found so far is forgotten, so it is asked again. Returns (primary listing, windows forgotten)."""
    _refuse_while_running(ctx)
    query = query.strip()
    if not skip and not query:
        raise InvalidQuery("a search name needs a query, or skip")
    if query and '"' not in query:
        query = f'"{query}"'
    found = [a.strip() for a in aliases if a.strip()] or re.findall(r'"([^"]+)"', query)
    symbol = _primary(ctx, symbol)
    forgotten = HistoryRepo(ctx.conn).set_name(symbol, query, tuple(dict.fromkeys(found)), skip,
                                               to_utc_iso(ctx.clock.utcnow()))
    return symbol, forgotten


def clear_search_name(ctx: AppContext, symbol: str) -> tuple[str, int]:
    _refuse_while_running(ctx)
    symbol = _primary(ctx, symbol)
    forgotten = HistoryRepo(ctx.conn).clear_name(symbol)
    if forgotten is None:
        raise InvalidQuery(f"{symbol} has no search name of yours")
    return symbol, forgotten


_plans: dict[tuple, tuple[float, list[Company]]] = {}


def history_companies(ctx: AppContext, watchlist: str = DEFAULT_LIST) -> list[Company]:
    """The list's companies, then every company on their industry maps, one line each (its primary
    listing): never a never-listed company (Tesla), a fund, an exchange product, a depositary
    receipt, a warrant or preferred share, a listing code for a name, or a second line of a
    company already taken under the same search name."""
    name, symbols = watchlist_symbols(ctx, watchlist)
    key = (str(ctx.config.db_path), name.casefold(), tuple(symbols))
    cached = _plans.get(key)
    if cached and time.monotonic() - cached[0] < PLAN_TTL_S:
        plan = cached[1]
    else:
        plan = _plan(ctx, symbols)
        _plans[key] = (time.monotonic(), plan)
    return [Company(c.symbol, c.name, c.main, c.of, search_for(ctx, c.symbol, c.name)) for c in plan]


def _plan(ctx: AppContext, symbols: list[str]) -> list[Company]:
    universe = UniverseRepo(ctx.conn)
    out: dict[str, Company] = {}
    names: dict[str, str] = {}

    def add(symbol: str, main: bool, of: str | None) -> str | None:
        p = universe.profile(symbol) if universe.resolve(symbol) else None
        if p is None or p.type.lower() != "equity" or never_company(ctx, symbol, user=True):
            return None
        key = p.primary_symbol
        if not main and (is_receipt(p.name) or is_receipt_symbol(key) or is_exchange_product(p.name)
                         or _NOT_SHARES_SYMBOL.search(key) or _NOT_SHARES_NAME.search(p.name)
                         or is_listing_code(p.name)):
            return None
        have = out.get(key.casefold())
        if have is None:
            same = default_search(p.name).query.casefold()
            if not main and same in names:
                key = names[same]
                have = out[key.casefold()]
            else:
                out[key.casefold()] = Company(key, p.name, main, (of,) if of else ())
                names.setdefault(same, key)
                return key
        if of and of not in have.of and not have.main:
            out[key.casefold()] = Company(have.symbol, have.name, have.main, (*have.of, of))
        return key

    mains = [s for s in (add(x, True, None) for x in symbols) if s]
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


def news_query(search: SearchName, start: date, end: date) -> str:
    """The search name inside Google's date operators. The answer is still checked against the
    window: Google's edges drift by the outlet's timezone."""
    return f"{search.query} after:{start.isoformat()} before:{end.isoformat()}"


async def backfill_history(ctx: AppContext, *, watchlist: str = DEFAULT_LIST, only: Sequence[str] | None = None,
                           start: date = NEWS_START, stages: Sequence[str] = STAGES,
                           stop: Callable[[], bool] | None = None, busy: Callable[[], str | None] | None = None,
                           on_progress: Callable[[str], None] = lambda _: None) -> HistoryRun:
    """Everything not yet stored, company by company (main list first). `only` limits it to
    those companies; `busy` names live work to wait for between requests; `stop` ends it there.
    The result is kept for the coverage card."""
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
    run.result.finished_at = to_utc_iso(ctx.clock.utcnow())
    MetaRepo(ctx.conn).set(LAST_RUN, json.dumps(asdict(run.result)))
    return run.result


def last_run(ctx: AppContext) -> HistoryRun | None:
    raw = MetaRepo(ctx.conn).get(LAST_RUN)
    return HistoryRun(**json.loads(raw)) if raw else None


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
    on_topic: float | None = None
    """Share of the quarter's searched headlines that name the company; None before any."""


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
    search: SearchName
    noisy: tuple[str, ...] = ()
    """Quarters with THIN or more articles of which fewer than NOISY name the company."""


@dataclass(frozen=True)
class Coverage:
    start: date
    quarters: tuple[str, ...]
    rows: tuple[CoverageRow, ...]
    windows_done: int
    """News windows finished (month re-asks of a full quarter included)."""
    windows_left: int
    """Quarter windows not yet asked, and filings and prices not yet tried."""
    last_run: HistoryRun | None = None
    caveat: str = ("today's list and maps taken back in time (survivorship); undated results are dropped, "
                   "not dated by the day fetched")


def coverage(ctx: AppContext, watchlist: str = DEFAULT_LIST, start: date = NEWS_START) -> Coverage:
    """What the backfill has stored per company and quarter, and how much is left, reading only."""
    companies = history_companies(ctx, watchlist)
    repo = HistoryRepo(ctx.conn)
    today = ctx.clock.utcnow().date()
    windows = news_windows(start, today)
    quarters = tuple(dict.fromkeys(_quarter(s) for s, _ in windows))
    symbols = [c.symbol for c in companies]
    counts: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for q in repo.quarters(symbols):
        counts.setdefault((q.symbol.casefold(), q.quarter), {})[q.source] = (q.articles, q.outlets)
    topic = repo.topic(symbols, NEWS)
    done = {src: repo.done(src) for src in (PRICES, FILINGS, NEWS)}
    tried = {src: {k[0] for k in done[src]} for src in (PRICES, FILINGS)}
    bars = BarRepo(ctx.conn)
    rows, left = [], 0
    for c in companies:
        key = c.symbol.casefold()
        cells = {}
        for quarter in quarters:
            found = counts.get((key, quarter), {})
            news, filings = found.get(NEWS, (0, 0)), found.get(FILINGS, (0, 0))
            kept, named = topic.get((key, quarter), (0, 0))
            if news[0] or filings[0]:
                cells[quarter] = Cell(news[0], filings[0], news[1] + filings[1], round(named / kept, 2) if kept else None)
        thin = tuple(q for q in quarters if not c.search.skip and (cells[q].news if q in cells else 0) < THIN)
        noisy = tuple(q for q, x in cells.items() if x.news >= THIN and x.on_topic is not None and x.on_topic < NOISY)
        if not c.search.skip:
            left += sum((key, s, e) not in done[NEWS] for s, e in windows)
        left += key not in tried[FILINGS] and _us(c.symbol)
        first = (span := bars.span([c.symbol])) and span[0]
        left += not _has_prices(first) and key not in tried[PRICES]
        rows.append(CoverageRow(c.symbol, c.name, c.main, c.of, cells, thin, first, c.search, noisy))
    return Coverage(start, quarters, tuple(rows), len(done[NEWS]), left, last_run(ctx))


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
        self.began = ctx.clock.utcnow()

    async def prices(self, client: RateLimitedClient) -> None:
        tried = {k[0] for k in self.repo.done(PRICES)}
        bars = BarRepo(self.ctx.conn)
        todo = [c for c in self.companies if c.symbol.casefold() not in tried
                and not _has_prices((span := bars.span([c.symbol])) and span[0])]
        for i, c in enumerate(todo, 1):
            await self._turn(f"price bars from {PRICE_START}: {c.symbol} ({i}/{len(todo)})")
            await self._price(client, c)

    async def _price(self, client: RateLimitedClient, c: Company) -> None:
        try:
            r = await backfill_listing(self.ctx, client, c.symbol, PRICE_START)
        except PriceUnavailable as exc:
            if exc.no_data:
                self._finish(Window(c.symbol, PRICES, PRICE_START, self.today, 0, 0, 0, 0, str(exc)), [])
            elif (resting := self._resting([s.name for s in price_sources(self.ctx) if s.enabled])) is not None:
                await self._locked_out(resting, PRICES)
                await self._price(client, c)
            else:
                self.result.failures.append(f"{c.symbol} prices: {exc}")
            return
        except UnknownSymbol as exc:
            self.result.failures.append(f"{c.symbol} prices: {exc}")
            return
        self.result.prices_added += r.added
        self._finish(Window(c.symbol, PRICES, PRICE_START, self.today, r.added, r.added, 0, 0, None), [])

    def _resting(self, sources: Sequence[str]) -> CircuitOpen | None:
        """The open breaker holding back every price source, when that is why none answered."""
        gate = SourceGate(self.ctx.conn, self.ctx.clock, self.ctx.config.sources)
        opened = []
        for source in sources:
            try:
                status = gate.status(source)
            except KeyError:
                continue
            if status.open_until is None:
                return None
            opened.append(CircuitOpen(source, status.reason, status.open_until))
        return max(opened, key=lambda x: x.retry_at) if opened else None

    async def filings(self, client: RateLimitedClient) -> None:
        contact = self.ctx.config.research.contact
        if not contact:
            self.result.failures.append('filings skipped: SEC needs [research] contact = "Name email"')
            return
        done = {k[0] for k in self.repo.done(FILINGS)}
        todo = [c for c in self.companies if _us(c.symbol) and c.symbol.casefold() not in done]
        for i, c in enumerate(todo, 1):
            await self._turn(f"SEC filings: {c.symbol} ({i}/{len(todo)})")
            await self._filings(client, c, contact)

    async def _filings(self, client: RateLimitedClient, c: Company, contact: str) -> None:
        try:
            cik, body = await submissions(client, SecTickerRepo(self.ctx.conn), self.ctx.clock, contact, c.symbol)
            filings = await filings_since(client, contact, body, cik, self.start)
        except NoFilings as exc:
            self._finish(Window(c.symbol, FILINGS, self.start, self.today, 0, 0, 0, 0, str(exc)), [])
            return
        except ToolError as exc:
            if isinstance(exc, Unavailable) and isinstance(exc.__cause__, CircuitOpen):
                await self._locked_out(exc.__cause__, FILINGS)
                await self._filings(client, c, contact)
            else:
                self.result.failures.append(f"{c.symbol} filings: {exc}")
            return
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
        plan = [(c, s, e) for c in self.companies if not c.search.skip for s, e in windows]
        failed = 0
        for i, (c, s, e) in enumerate(plan, 1):
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
            # Month by month for the main list only: the maps' companies are context, one quarter each.
            if not c.main or w.found < GOOGLE_CAP:
                continue
            for m in _months(s, e):
                ms, me = max(m, s), _add_months(m, 1)
                if (ms, me) != (s, e) and (c.symbol.casefold(), ms, me) not in done:
                    await self._search(client, c, ms, me, f"{label}, month {ms:%Y-%m}")

    async def _search(self, client: RateLimitedClient, c: Company, s: date, e: date, label: str) -> Window | None:
        await self._turn(label)
        q = news_query(c.search, s, e)
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
        w = Window(c.symbol, NEWS, s, e, len(found), len(ids), len(found) - len(dated), len(dated) - len(inside),
                   on_topic=sum(mentions(a.title, c.search.aliases) for a in inside))
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
        """Between requests: stop when told, and wait while live work runs (or is about to)."""
        self._check()
        while self.busy and (other := self.busy()):
            self.on_progress(f"waiting for {other}")
            await self.ctx.clock.sleep(WAIT_STEP_S)
            self._check()
        hours = max((self.ctx.clock.utcnow() - self.began).total_seconds() / 3600, 1 / 60)
        self.on_progress(f"{label}; {self.result.requests} requests, {self.result.requests / hours:.0f} an hour")

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


def _refuse_while_running(ctx: AppContext) -> None:
    """A run reads each company's search name once, at its start: a name changed under it would
    leave the windows it has still to ask searched by the old one and never asked again."""
    if any(s.job == JOB and s.stale_reason is None for s in job_statuses(ctx)):
        raise JobAlreadyRunning("the history backfill is running: cancel it (Activity tab), then change the "
                                "search name; the next run asks that company's news again")


def _primary(ctx: AppContext, symbol: str) -> str:
    return UniverseRepo(ctx.conn).profile(resolve_symbol(ctx, symbol).symbol).primary_symbol


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
