"""What every research tool shares: where found evidence is stored and how it is shown,
one way to fetch through the rate gate, and forgiving input handling."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime

import httpx

from oracle.clock import Clock
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research.budget import cut
from oracle.research.news import Article, end_of
from oracle.research.tools import ToolError
from oracle.research.trace import NOT_ABOUT, Evidence
from oracle.research.web import domain
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso

# Enough to see a story's shape without flooding a small model's context.
MAX_ARTICLES = 8
MAX_TITLE = 140
MAX_SNIPPET = 100


class Unavailable(ToolError):
    """A passing failure (the network, a rate limit, an open breaker): worth trying again later."""


class Refused(ToolError):
    """The source answered with a status the caller does not accept."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class Shelf:
    """Where tools put what they find: stored once per URL, cut to the as-of date, newest
    first, and shown with the ids findings cite."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock, on_shown: Callable[[list[int], str | None], None],
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
        return "\n".join(_line(i, a) for i, a in zip(self.keep(newest, source, query), newest))

    def keep_text(self, article_id: int, url: str, text: str) -> None:
        """What reading an article gave, so no later reading fetches it again."""
        self._articles.save_text(article_id, url, text, to_utc_iso(self._clock.utcnow()))

    def keep_unreadable(self, article_id: int, why: str) -> None:
        self._articles.save_read_failure(article_id, why, False, to_utc_iso(self._clock.utcnow()))

    def keep(self, articles: list[Article], source: str, query: str | None = None) -> list[int]:
        """Store and mark as shown to the run with the query that found them; the ids, in order."""
        ids = self._articles.save(articles, source, to_utc_iso(self._clock.utcnow()), query)
        self._on_shown(ids, query)
        return ids


def evidence_line(article_id: int, published: datetime | None, outlet: str | None, title: str) -> str:
    """How an item of evidence reads to the model, wherever it is listed."""
    day = published.date().isoformat() if published else "undated"
    return f"[a{article_id}] {day} {outlet or 'unknown outlet'}: {cut(title, MAX_TITLE)}"


def evidence_entry(e: Evidence) -> str:
    """An evidence line with the facts read from the article under it, each citing it."""
    line = evidence_line(e.id, e.published, e.outlet, e.title)
    if e.read == NOT_ABOUT:
        return f"{line} (read: {NOT_ABOUT})"
    return line + "".join(f"\n  - {f} [a{e.id}]" for f in e.facts)


def _line(article_id: int, a: Article) -> str:
    line = evidence_line(article_id, a.published, a.outlet, a.title)
    return f"{line} | {cut(a.summary, MAX_SNIPPET)}" if a.summary else line


def symbol(input: str, default: str) -> str:
    # Small models sometimes leak JSON punctuation into the input.
    return input.strip(" '\"{}[]") or default


def query(input: str) -> str:
    text = input.strip()
    if not text:
        raise ToolError("give a search query")
    return text


async def fetch(client: RateLimitedClient, source: str, label: str, url: str, *, params: dict | None = None,
                headers: dict | None = None, max_bytes: int | None = None, follow_redirects: bool | None = None,
                accept: tuple[int, ...] = (200,), count_403: bool = True, form: dict | None = None,
                breaker: bool = True) -> httpx.Response:
    """A GET, or with `form` a POST, through the gate whose failures read as tool errors the
    model can act on. Redirects are followed for a GET and not for a POST, unless told."""
    if follow_redirects is None:
        # httpx follows a POST's 302 or 303 as a GET, which the caller never asked for.
        follow_redirects = form is None
    options = dict(params=params, headers=headers, max_bytes=max_bytes, follow_redirects=follow_redirects,
                   count_403=count_403, breaker=breaker)
    try:
        resp = await (client.post(source, url, data=form, **options) if form is not None else
                      client.get(source, url, **options))
    except (CircuitOpen, RequestFailed) as exc:
        raise Unavailable(f"{label} unavailable ({exc}); try another tool") from exc
    # An address httpx cannot encode (an IDNA label, a control character) or a body it cannot
    # decode: the same next time, so not passing.
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise ToolError(f"{label} could not be read ({type(exc).__name__}: {exc})") from exc
    if resp.status_code not in accept:
        raise Refused(f"{label} answered HTTP {resp.status_code}", resp.status_code)
    return resp
