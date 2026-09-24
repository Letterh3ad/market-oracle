"""News feeds: Google News RSS and the GDELT DOC API, both dated, both cut to an as-of day."""

from __future__ import annotations

from oracle.net.client import RateLimitedClient
from oracle.research.news import (
    ENGLISH_EDITION, GDELT_DOC, GOOGLE_NEWS, FeedUnreadable, gdelt_params, google_news_params, parse_gdelt,
    parse_google_news,
)
from oracle.research.queries import EDITION, LOCAL, LOCAL_NEWS, NEWS
from oracle.research.tools import ToolError
from oracle.services.research_tools.shelf import Shelf, fetch, query


class NewsSearch:
    """Google News RSS: recent headlines from many outlets, dated."""

    name = NEWS
    description = "recent news headlines with dates and outlets; input: a search query"
    _edition = ENGLISH_EDITION

    def __init__(self, client: RateLimitedClient, shelf: Shelf) -> None:
        self._client = client
        self._shelf = shelf

    async def run(self, input: str) -> str:
        q = query(input)
        resp = await fetch(self._client, "google_news", "news search", GOOGLE_NEWS,
                           params=google_news_params(q, self._shelf.as_of, self._edition))
        try:
            found = parse_google_news(resp.text)
        except FeedUnreadable as exc:
            raise ToolError(f"news search sent an unreadable feed ({exc})") from exc
        return self._shelf.show(found, "google_news", q)


class LocalNewsSearch(NewsSearch):
    """Google News in the listing's own country and language. A tool of its own rather than
    a flag in the input: the loop's schema limits actions to the tools' names, so a small
    model picks it reliably, where a flag it would have to remember to write."""

    name = LOCAL_NEWS

    def __init__(self, client: RateLimitedClient, shelf: Shelf, country: str) -> None:
        super().__init__(client, shelf)
        self._edition = EDITION[country]
        language = LOCAL[country][1]
        self.description = (f"news headlines from Google News {country}, in {language}, with dates and outlets; "
                            f"input: a search query ({language} words find the most)")


class GdeltSearch:
    """GDELT DOC API: worldwide news in many languages, timestamped when first seen."""

    name = "gdelt_search"
    description = ("worldwide news in many languages, with dates; input: a search query "
                   '(put exact phrases in "quotes"; very short words are refused)')

    def __init__(self, client: RateLimitedClient, shelf: Shelf) -> None:
        self._client = client
        self._shelf = shelf

    async def run(self, input: str) -> str:
        q = query(input)
        resp = await fetch(self._client, "gdelt", "GDELT", GDELT_DOC, params=gdelt_params(q, self._shelf.as_of))
        try:
            found = parse_gdelt(resp.text)
        except FeedUnreadable as exc:
            raise ToolError(f"GDELT refused the query: {exc}") from exc
        return self._shelf.show(found, "gdelt", q)
