"""A fake Google News RSS search, shaped like the real feed (item title "Headline - Outlet",
a <source> element naming the outlet, RFC 2822 pubDate), with the stub pages and link decoder
behind its article links."""

from __future__ import annotations

import json
from email.utils import format_datetime
from urllib.parse import parse_qs
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
        self.decoded: dict[str, str] = {}
        """stub id -> the article address Google's decoder gives for it"""
        self.stub_pages: list[httpx.Request] = []
        self.stub_status: int | str = 200
        """200, another status (a 3xx goes to consent), or "down" for a transport error"""
        self.stub_padding = 0
        """bytes of page ahead of the signature"""
        self.decodes: list[httpx.Request] = []
        self.decoder_refuses = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/rss/articles/"):
            return self._stub_page(request)
        if request.url.path == "/_/DotsSplashUi/data/batchexecute":
            return self._decode(request)
        query = request.url.params.get("q", "")
        self.queries.append(query)
        self.params.append(dict(request.url.params))
        if self.status != 200:
            return httpx.Response(self.status)
        items = "".join(_item(*r) for r in self.results.get(query, []))
        xml = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(query)}</title>{items}</channel></rss>'
        return httpx.Response(200, text=xml, headers={"Content-Type": "application/xml"})

    def _stub_page(self, request: httpx.Request) -> httpx.Response:
        self.stub_pages.append(request)
        stub = request.url.path.rsplit("/", 1)[1]
        if self.stub_status == "down":
            raise httpx.ConnectError("down", request=request)
        if self.stub_status != 200:
            return httpx.Response(self.stub_status, headers={"Location": "https://consent.google.com/m"})
        if "SOCS=" not in request.headers.get("cookie", ""):
            return httpx.Response(302, headers={"Location": "https://consent.google.com/m"})
        if stub not in self.decoded:
            return httpx.Response(200, text="<html><body>no such article</body></html>")
        page = f'<html>{" " * self.stub_padding}<div data-n-a-id="{stub}" data-n-a-ts="1790261271" data-n-a-sg="sig{stub[-4:]}"></div></html>'
        return httpx.Response(200, text=page, headers={"Content-Type": "text/html"})

    def _decode(self, request: httpx.Request) -> httpx.Response:
        self.decodes.append(request)
        if request.method != "POST":
            return httpx.Response(405)
        [[[_, inner, _, _]]] = json.loads(parse_qs(request.content.decode())["f.req"][0])
        _, _, stub, ts, sig = json.loads(inner)
        if self.decoder_refuses or stub not in self.decoded or (ts, sig) != (1790261271, f"sig{stub[-4:]}"):
            return httpx.Response(200, text=_XSSI + '[["er",null,null,null,null,400]]')
        answer = json.dumps(["garturlres", self.decoded[stub], 1])
        return httpx.Response(200, text=_XSSI + json.dumps([["wrb.fr", "Fbv4je", answer, None, None, None, "generic"]]))


_XSSI = ")]}'\n\n"


def _item(url, headline, outlet, published) -> str:
    date = f"<pubDate>{format_datetime(published)}</pubDate>" if published else ""
    return (f"<item><title>{escape(headline)} - {escape(outlet)}</title><link>{escape(url)}</link>"
            f"<guid isPermaLink=\"false\">{escape(url)}</guid>{date}"
            f"<description>&lt;a href=\"{escape(url)}\"&gt;{escape(headline)}&lt;/a&gt;</description>"
            f"<source url=\"https://{outlet.lower().replace(' ', '')}.com\">{escape(outlet)}</source></item>")
