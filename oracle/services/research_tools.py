"""The tools a research run can call, bound to the app's data and network budget."""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Callable

from oracle.clock import Clock
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research.news import GOOGLE_NEWS, Article, google_news_params, parse_google_news
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


class MarketData:
    """Stored prices and fundamentals only: no fetching inside a research run."""

    name = "market_data"
    description = ("latest price, recent moves, fundamentals and baseline lean of a listing; "
                   "input: its symbol, or empty for the company researched")

    def __init__(self, ctx: AppContext, listing: str) -> None:
        self._ctx = ctx
        self._listing = listing

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
        if view.price is None:
            parts.append("No prices stored.")
        else:
            bar = view.price.bar
            parts.append(f"Last close {bar.close:,.2f} {bar.currency} on {bar.date.isoformat()}.")
            bars = price_history(self._ctx, p.symbol, 64)
            for label, back in (("1 day", 1), ("1 month", 21), ("3 months", 63)):
                if len(bars) > back:
                    parts.append(f"Change over {label}: {bars[-1].close / bars[-1 - back].close - 1:+.1%}.")
        f = view.fundamentals
        if f is None:
            parts.append("No fundamentals stored.")
        else:
            known = ", ".join(f"{fig.label} {_figure(fig.kind, v, f.currency)}" for fig in FIGURES
                              if (v := getattr(f, fig.name)) is not None)
            parts.append(f"Fundamentals as of {f.as_of.isoformat()}: {known or 'none known'}.")
        if view.lean is not None:
            stale = " (out of date)" if view.lean_stale else ""
            parts.append(f"Baseline lean: {view.lean.label} {view.lean.probability:.0%}{stale}.")
        return " ".join(parts)



def _figure(kind: str, value: float, currency: str | None) -> str:
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "money":
        for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(value) >= size:
                return f"{value / size:.2f}{suffix} {currency or ''}".rstrip()
        return f"{value:,.0f} {currency or ''}".rstrip()
    return f"{value:.2f}"


class NewsSearch:
    """Google News RSS: recent headlines from many outlets, dated. Articles are stored once
    per URL and shown with ids the findings cite."""

    name = "news_search"
    description = "recent news headlines with dates and outlets; input: a search query"

    def __init__(self, client: RateLimitedClient, conn: sqlite3.Connection, clock: Clock,
                 on_shown: Callable[[list[int]], None]) -> None:
        self._client = client
        self._articles = ArticleRepo(conn)
        self._clock = clock
        self._on_shown = on_shown

    async def run(self, input: str) -> str:
        query = input.strip()
        if not query:
            raise ToolError("give a search query")
        try:
            resp = await self._client.get("google_news", GOOGLE_NEWS, params=google_news_params(query))
        except (CircuitOpen, RequestFailed) as exc:
            raise ToolError(f"news search unavailable ({exc}); try another tool") from exc
        if resp.status_code != 200:
            raise ToolError(f"news search answered HTTP {resp.status_code}")
        try:
            found = parse_google_news(resp.text)
        except ET.ParseError as exc:
            raise ToolError(f"news search sent an unreadable feed ({exc})") from exc
        return self.show(found, "google_news", query)

    def show(self, found: list[Article], source: str, query: str) -> str:
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
