"""A fake Google News RSS search, shaped like the real feed (item title "Headline - Outlet",
a <source> element naming the outlet, RFC 2822 pubDate)."""

from __future__ import annotations

from email.utils import format_datetime
from xml.sax.saxutils import escape

import httpx


class FakeGoogleNews:
    hosts = {"news.google.com"}

    def __init__(self) -> None:
        self.results: dict[str, list[tuple]] = {}
        """query -> [(url, headline, outlet, published datetime or None)]"""
        self.queries: list[str] = []
        self.params: list[dict] = []
        self.status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q", "")
        self.queries.append(query)
        self.params.append(dict(request.url.params))
        if self.status != 200:
            return httpx.Response(self.status)
        items = "".join(_item(*r) for r in self.results.get(query, []))
        xml = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(query)}</title>{items}</channel></rss>'
        return httpx.Response(200, text=xml, headers={"Content-Type": "application/xml"})


def _item(url, headline, outlet, published) -> str:
    date = f"<pubDate>{format_datetime(published)}</pubDate>" if published else ""
    return (f"<item><title>{escape(headline)} - {escape(outlet)}</title><link>{escape(url)}</link>"
            f"<guid isPermaLink=\"false\">{escape(url)}</guid>{date}"
            f"<description>&lt;a href=\"{escape(url)}\"&gt;{escape(headline)}&lt;/a&gt;</description>"
            f"<source url=\"https://{outlet.lower().replace(' ', '')}.com\">{escape(outlet)}</source></item>")
