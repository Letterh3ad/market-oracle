"""News search results. RSS parsing adapted from the stalker's feed reader: ElementTree
expands no external entities and raises on undefined ones, so entity attacks fail closed."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

GOOGLE_NEWS = "https://news.google.com/rss/search"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_RECORDS = 50


class FeedUnreadable(ValueError):
    """The source answered, but not with results (GDELT explains bad queries in plain text)."""


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    outlet: str | None
    published: datetime | None
    """Timezone-aware, or None when the feed gave no usable date."""
    summary: str | None = field(default=None, compare=False)
    """A search snippet, shown with the article but not stored."""
    body: str | None = field(default=None, compare=False)
    """Stored text, for evidence written by Market Oracle itself."""


def end_of(day: date) -> datetime:
    """The first instant after `day`, UTC: an as-of run sees only what came before it."""
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc)


def google_news_params(query: str, as_of: date | None = None) -> dict:
    # Google's `before:` narrows the search; results are still filtered on their dates.
    q = f"{query} before:{(as_of + timedelta(days=1)).isoformat()}" if as_of else query
    return {"q": q, "hl": "en-GB", "gl": "GB", "ceid": "GB:en"}


def gdelt_params(query: str, as_of: date | None = None) -> dict:
    params = {"query": query, "mode": "ArtList", "format": "json", "maxrecords": str(GDELT_RECORDS),
              "sort": "DateDesc"}
    if as_of:
        # GDELT's DOC API searches roughly the last three months at most.
        params["startdatetime"] = (as_of - timedelta(days=90)).strftime("%Y%m%d000000")
        params["enddatetime"] = as_of.strftime("%Y%m%d235959")
    return params


def parse_gdelt(text: str) -> list[Article]:
    try:
        body = json.loads(text)
    except ValueError as exc:
        raise FeedUnreadable(text.strip()[:200] or "empty answer") from exc
    out = []
    for a in body.get("articles", []) if isinstance(body, dict) else []:
        url, title = (a.get("url") or "").strip(), (a.get("title") or "").strip()
        if url and title:
            out.append(Article(url, title, a.get("domain") or None, _gdelt_seen(a.get("seendate"))))
    return out


def _gdelt_seen(raw: str | None) -> datetime | None:
    try:
        return datetime.strptime(raw or "", "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_google_news(xml: str) -> list[Article]:
    """Items as articles. Google titles read "Headline - Outlet"; the outlet also comes
    in <source>, so the suffix is dropped when they agree."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise FeedUnreadable(str(exc)) from exc
    out = []
    for item in root.iter("item"):
        url = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not url or not title:
            continue
        outlet = (item.findtext("source") or "").strip() or None
        if outlet and title.endswith(f" - {outlet}"):
            title = title[: -len(outlet) - 3].rstrip()
        out.append(Article(url, title, outlet, _rfc2822(item.findtext("pubDate"))))
    return out


def _rfc2822(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        return None
    # A naive date would need a guessed timezone; better no date than a wrong one.
    return value if value.tzinfo is not None else None
