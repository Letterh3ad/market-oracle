"""The tools a research run can call, bound to the app's data and network budget. In an
as-of run every tool sees only what existed by the end of that day."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date

from oracle.clock import Clock
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research.news import (
    GDELT_DOC, GOOGLE_NEWS, Article, FeedUnreadable, end_of, gdelt_params, google_news_params, parse_gdelt,
    parse_google_news,
)
from oracle.research.tools import ToolError
from oracle.services.company_service import FIGURES, company_view
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.prices_service import price_history
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso

# Enough to see a story's shape without flooding a small model's context.
MAX_ARTICLES = 8
MAX_TITLE = 140
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

    def show(self, found: list[Article], source: str, query: str) -> str:
        if self.as_of:
            # An undated article might be from after the cutoff, so it cannot be used.
            cutoff = end_of(self.as_of)
            found = [a for a in found if a.published is not None and a.published < cutoff]
        newest = sorted(found, key=lambda a: (a.published is not None, a.published), reverse=True)[:MAX_ARTICLES]
        if not newest:
            return f"No articles found for {query!r}."
        ids = self._articles.save(newest, source, to_utc_iso(self._clock.utcnow()))
        self._on_shown(ids)
        return "\n".join(_line(i, a) for i, a in zip(ids, newest))


def _line(article_id: int, a: Article) -> str:
    day = a.published.date().isoformat() if a.published else "undated"
    title = a.title if len(a.title) <= MAX_TITLE else a.title[: MAX_TITLE - 1] + "…"
    return f"[a{article_id}] {day} {a.outlet or 'unknown outlet'}: {title}"


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
