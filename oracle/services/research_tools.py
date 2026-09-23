"""The tools a research run can call, bound to the app's data and network budget. In an
as-of run every tool sees only what existed by the end of that day."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit

from oracle.clock import Clock
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.net.gate import SourceGate
from oracle.research import edgar
from oracle.research.news import (
    GDELT_DOC, GOOGLE_NEWS, Article, FeedUnreadable, end_of, gdelt_params, google_news_params, parse_gdelt,
    parse_google_news,
)
from oracle.research.tools import ToolError
from oracle.research.web import WebSearcher, WebSearchFailed, page_text, unsafe_url
from oracle.services.company_service import FIGURES, company_view
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.prices_service import price_history
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso
from oracle.store.sec import SecTickerRepo

# Enough to see a story's shape without flooding a small model's context.
MAX_ARTICLES = 8
MAX_TITLE = 140
MAX_SNIPPET = 100
# Fewer than news: with a snippet each, five fill an observation.
WEB_RESULTS = 5
# A page is read up to here (bytes) and shown up to MAX_PAGE_TEXT characters.
MAX_PAGE_BYTES = 2_000_000
MAX_PAGE_TEXT = 1300
READABLE = ("text/html", "application/xhtml+xml", "text/plain")
# Trading days of history behind the "3 months" move, plus the last close.
RECENT_BARS = 64


class MarketData:
    """Stored prices and fundamentals only: no fetching inside a research run."""

    name = "market_data"
    description = ("latest price, recent moves, fundamentals and baseline lean of a listing; "
                   "input: its symbol, or empty for the company researched")

    def __init__(self, ctx: AppContext, listing: str, as_of: date | None = None) -> None:
        self._ctx = ctx
        self._listing = listing
        self._as_of = as_of

    async def run(self, input: str) -> str:
        # Small models sometimes leak JSON punctuation into the input.
        symbol = input.strip(" '\"{}[]") or self._listing
        try:
            view = await company_view(self._ctx, symbol, refresh=False)
        except ServiceError as exc:
            raise ToolError(str(exc)) from exc
        p = view.profile
        parts = [f"{p.symbol}: {p.name}, {p.exchange or 'exchange unknown'}, {p.country or 'country unknown'}, "
                 f"{p.sector or 'sector unknown'}."]
        bars = [b for b in price_history(self._ctx, p.symbol) if self._known(b.date)][-RECENT_BARS:]
        if not bars:
            parts.append("No prices stored.")
        else:
            last = bars[-1]
            parts.append(f"Last close {last.close:,.2f} {last.currency} on {last.date.isoformat()}.")
            for label, back in (("1 day", 1), ("1 month", 21), ("3 months", 63)):
                if len(bars) > back:
                    parts.append(f"Change over {label}: {last.close / bars[-1 - back].close - 1:+.1%}.")
        f = view.fundamentals
        if f is None or not self._known(f.as_of):
            parts.append("No fundamentals stored.")
        else:
            known = ", ".join(f"{fig.label} {_figure(fig.kind, v, f.currency)}" for fig in FIGURES
                              if (v := getattr(f, fig.name)) is not None)
            parts.append(f"Fundamentals as of {f.as_of.isoformat()}: {known or 'none known'}.")
        if view.lean is not None and self._known(view.lean.as_of):
            stale = " (out of date)" if view.lean_stale else ""
            parts.append(f"Baseline lean: {view.lean.label} {view.lean.probability:.0%}{stale}.")
        return " ".join(parts)

    def _known(self, day: date) -> bool:
        return self._as_of is None or day <= self._as_of


def _figure(kind: str, value: float, currency: str | None) -> str:
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "money":
        for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(value) >= size:
                return f"{value / size:.2f}{suffix} {currency or ''}".rstrip()
        return f"{value:,.0f} {currency or ''}".rstrip()
    return f"{value:.2f}"


class Shelf:
    """Where news tools put what they find: stored once per URL, cut to the as-of date,
    newest first, and shown with the ids findings cite."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock, on_shown: Callable[[list[int]], None],
                 as_of: date | None = None) -> None:
        self._articles = ArticleRepo(conn)
        self._clock = clock
        self._on_shown = on_shown
        self.as_of = as_of

    def show(self, found: list[Article], source: str, query: str, limit: int = MAX_ARTICLES) -> str:
        if self.as_of:
            # An undated article might be from after the cutoff, so it cannot be used.
            cutoff = end_of(self.as_of)
            found = [a for a in found if a.published is not None and a.published < cutoff]
        newest = sorted(found, key=lambda a: (a.published is not None, a.published), reverse=True)[:limit]
        if not newest:
            return f"No articles found for {query!r}."
        return "\n".join(_line(i, a) for i, a in zip(self.keep(newest, source), newest))

    def keep(self, articles: list[Article], source: str) -> list[int]:
        """Store and mark as shown to the run; the ids, in order."""
        ids = self._articles.save(articles, source, to_utc_iso(self._clock.utcnow()))
        self._on_shown(ids)
        return ids


def _line(article_id: int, a: Article) -> str:
    day = a.published.date().isoformat() if a.published else "undated"
    line = f"[a{article_id}] {day} {a.outlet or 'unknown outlet'}: {_cut(a.title, MAX_TITLE)}"
    return f"{line} | {_cut(a.summary, MAX_SNIPPET)}" if a.summary else line


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


async def _fetch(client: RateLimitedClient, source: str, label: str, url: str, params: dict) -> str:
    try:
        resp = await client.get(source, url, params=params)
    except (CircuitOpen, RequestFailed) as exc:
        raise ToolError(f"{label} unavailable ({exc}); try another tool") from exc
    if resp.status_code != 200:
        raise ToolError(f"{label} answered HTTP {resp.status_code}")
    return resp.text


def _query(input: str) -> str:
    query = input.strip()
    if not query:
        raise ToolError("give a search query")
    return query


class NewsSearch:
    """Google News RSS: recent headlines from many outlets, dated."""

    name = "news_search"
    description = "recent news headlines with dates and outlets; input: a search query"

    def __init__(self, client: RateLimitedClient, shelf: Shelf) -> None:
        self._client = client
        self._shelf = shelf

    async def run(self, input: str) -> str:
        query = _query(input)
        text = await _fetch(self._client, "google_news", "news search", GOOGLE_NEWS,
                            google_news_params(query, self._shelf.as_of))
        try:
            found = parse_google_news(text)
        except FeedUnreadable as exc:
            raise ToolError(f"news search sent an unreadable feed ({exc})") from exc
        return self._shelf.show(found, "google_news", query)


class GdeltSearch:
    """GDELT DOC API: worldwide news in many languages, timestamped when first seen."""

    name = "gdelt_search"
    description = ("worldwide news in many languages, with dates; input: a search query "
                   '(put exact phrases in "quotes"; very short words are refused)')

    def __init__(self, client: RateLimitedClient, shelf: Shelf) -> None:
        self._client = client
        self._shelf = shelf

    async def run(self, input: str) -> str:
        query = _query(input)
        text = await _fetch(self._client, "gdelt", "GDELT", GDELT_DOC, gdelt_params(query, self._shelf.as_of))
        try:
            found = parse_gdelt(text)
        except FeedUnreadable as exc:
            raise ToolError(f"GDELT refused the query: {exc}") from exc
        return self._shelf.show(found, "gdelt", query)


class WebSearch:
    """The open web through `ddgs`. It makes its own HTTP calls, so the gate is driven here."""

    name = "web_search"
    description = "web pages matching a query, with a snippet each (undated); input: a search query"

    def __init__(self, searcher: WebSearcher, gate: SourceGate, shelf: Shelf) -> None:
        self._searcher = searcher
        self._gate = gate
        self._shelf = shelf

    async def run(self, input: str) -> str:
        query = _query(input)
        try:
            await self._gate.acquire("ddgs")
        except CircuitOpen as exc:
            raise ToolError(f"web search unavailable ({exc}); try another tool") from exc
        try:
            results = await self._searcher.search(query, WEB_RESULTS)
        except WebSearchFailed as exc:
            if exc.rate_limited:
                self._gate.record("ddgs", 429)
                raise ToolError(f"web search is rate limited ({exc}); try another tool") from exc
            self._gate.record_transport_error("ddgs")
            raise ToolError(f"web search failed ({exc})") from exc
        self._gate.record("ddgs", 200)
        found = [Article(r.url, r.title or r.url, _domain(r.url), None, r.snippet or None) for r in results]
        return self._shelf.show(found, "ddgs", query, WEB_RESULTS)


class ReadPage:
    """A web page as plain text, stored as evidence like an article."""

    name = "read_page"
    description = "the text of a web page; input: its http(s) address from a search result"

    def __init__(self, client: RateLimitedClient, shelf: Shelf, contact: str | None = None) -> None:
        self._client = client
        self._shelf = shelf
        self._contact = contact

    async def run(self, input: str) -> str:
        url = input.strip(" '\"<>")
        if why := unsafe_url(url):
            raise ToolError(why)
        if _domain(url) == "news.google.com":
            raise ToolError("Google News links only redirect; search its headline with web_search instead")
        source, headers = "web", None
        if edgar.is_sec(url):
            # SEC refuses requests without a declared contact, and has its own budget.
            source, headers = "sec_edgar", {"User-Agent": edgar.user_agent(_need_contact(self._contact))}
        try:
            resp = await self._client.get(source, url, headers=headers, max_bytes=MAX_PAGE_BYTES)
        except (CircuitOpen, RequestFailed) as exc:
            raise ToolError(f"could not read the page ({exc})") from exc
        if resp.status_code != 200:
            raise ToolError(f"the page answered HTTP {resp.status_code}")
        kind = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if kind not in READABLE:
            raise ToolError(f"not a web page ({kind or 'unknown type'})")
        title, text = page_text(resp.text) if kind != "text/plain" else ("", resp.text.strip())
        title = _cut(title or url, 300)
        [article_id] = self._shelf.keep([Article(url, title, _domain(url), None)], "web")
        cut = len(text) > MAX_PAGE_TEXT or resp.extensions.get("oracle_truncated", False)
        body = (text[:MAX_PAGE_TEXT] + (" [cut]" if cut else "")) or "(no readable text)"
        return f"[a{article_id}] {_domain(url)}: {title}\n{body}"


# SEC's ticker map changes slowly; a week-old copy only misses brand-new listings.
TICKERS_MAX_AGE = timedelta(days=7)


def _need_contact(contact: str | None) -> str:
    if not contact:
        raise ToolError('SEC needs a contact: set [research] contact = "Name email" in oracle.toml')
    return contact


class Filings:
    """SEC EDGAR: a US-listed company's recent key filings, dated, readable with read_page."""

    name = "filings"
    description = ("a US-listed company's recent SEC filings (annual, quarterly and event reports), dated; "
                   "input: its symbol, or empty for the company researched")

    def __init__(self, client: RateLimitedClient, conn: sqlite3.Connection, clock: Clock, shelf: Shelf,
                 listing: str, contact: str | None) -> None:
        self._client = client
        self._tickers = SecTickerRepo(conn)
        self._clock = clock
        self._shelf = shelf
        self._listing = listing
        self._contact = contact

    async def run(self, input: str) -> str:
        symbol = input.strip(" '\"{}[]").upper() or self._listing
        # Yahoo marks non-US listings with an exchange suffix (VOD.L, 7203.T); US share
        # classes use a dash (BRK-B).
        if "." in symbol:
            return f"No SEC filings: {symbol} is not a US listing."
        headers = {"User-Agent": edgar.user_agent(_need_contact(self._contact))}
        await self._refresh_tickers(headers)
        found = self._tickers.lookup(symbol) or self._tickers.lookup(symbol.replace("-", "."))
        if found is None:
            return f"No SEC filer with ticker {symbol}."
        cik, _ = found
        filings = edgar.parse_filings(await self._json(edgar.submissions_url(cik), headers), cik)
        articles = [Article(f.url, f"{f.form} {f.description}".strip(), "SEC",
                            datetime.combine(f.filed, time(), timezone.utc)) for f in filings]
        return self._shelf.show(articles, "sec_edgar", symbol)

    async def _refresh_tickers(self, headers: dict) -> None:
        fetched = self._tickers.fetched_at()
        if fetched and self._clock.utcnow() - datetime.fromisoformat(fetched) < TICKERS_MAX_AGE:
            return
        tickers = edgar.parse_tickers(await self._json(edgar.TICKERS_URL, headers))
        if tickers:
            self._tickers.replace(tickers, to_utc_iso(self._clock.utcnow()))

    async def _json(self, url: str, headers: dict) -> object:
        try:
            resp = await self._client.get("sec_edgar", url, headers=headers)
        except (CircuitOpen, RequestFailed) as exc:
            raise ToolError(f"SEC unavailable ({exc}); try another tool") from exc
        if resp.status_code != 200:
            raise ToolError(f"SEC answered HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ToolError(f"SEC sent something unreadable ({exc})") from exc
