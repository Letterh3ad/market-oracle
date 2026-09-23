"""News search results. RSS parsing adapted from the stalker's feed reader: ElementTree
expands no external entities and raises on undefined ones, so entity attacks fail closed."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

GOOGLE_NEWS = "https://news.google.com/rss/search"


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    outlet: str | None
    published: datetime | None
    """Timezone-aware, or None when the feed gave no usable date."""


def google_news_params(query: str) -> dict:
    return {"q": query, "hl": "en-GB", "gl": "GB", "ceid": "GB:en"}


def parse_google_news(xml: str) -> list[Article]:
    """Items as articles. Google titles read "Headline - Outlet"; the outlet also comes
    in <source>, so the suffix is dropped when they agree."""
    out = []
    for item in ET.fromstring(xml).iter("item"):
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
