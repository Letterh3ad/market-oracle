"""Web search and reading pages as text. HTML stripping adapted from the stalker's
`wfm/news/html.py`: stdlib html.parser, no dependency for what twenty lines do."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit

_WHITESPACE = re.compile(r"\s+")
# Never text worth reading, whatever is inside: code, styling, and page furniture.
_OPAQUE = frozenset({"script", "style", "noscript", "svg", "template", "nav", "footer", "aside", "form", "head"})
# Characters of body text below which a page probably renders with scripts.
THIN_PAGE = 300
_BLOCK = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"})


@dataclass(frozen=True)
class WebResult:
    url: str
    title: str
    snippet: str


class WebSearchFailed(RuntimeError):
    def __init__(self, message: str, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


class WebSearcher(Protocol):
    async def search(self, query: str, max_results: int) -> list[WebResult]: ...


class DdgsSearcher:
    """`ddgs` makes its own HTTP calls, so pacing and the breaker are the caller's job."""

    def __init__(self, timeout_s: int = 10) -> None:
        self._timeout = timeout_s

    async def search(self, query: str, max_results: int) -> list[WebResult]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, RatelimitException

        def run() -> list[dict]:
            return DDGS(timeout=self._timeout).text(query, max_results=max_results)

        try:
            rows = await asyncio.to_thread(run)
        except RatelimitException as exc:
            raise WebSearchFailed(str(exc), rate_limited=True) from exc
        except DDGSException as exc:
            # ddgs reports "no results" as an exception too.
            if "no results" in str(exc).lower():
                return []
            raise WebSearchFailed(str(exc)) from exc
        return [WebResult(r.get("href", ""), r.get("title", ""), r.get("body", "")) for r in rows if r.get("href")]


def domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def unsafe_url(url: str) -> str | None:
    """Why the page reader must not open `url`, or None. Links come from web pages the
    model read, so they may point back into this machine or the local network."""
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises for a malformed port, as urlsplit does for a bad host
    except ValueError:
        return "that is not a valid address"
    if parts.scheme not in ("http", "https"):
        return "only http and https pages can be read"
    host = (parts.hostname or "").lower()
    if not host:
        return "the address has no host"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return "local addresses cannot be read"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not ip.is_global:
        return "private and local network addresses cannot be read"
    return None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.description = ""
        self._opaque = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "meta":
            a = dict(attrs)
            if (a.get("name") or a.get("property") or "").lower() in ("description", "og:description"):
                self.description = self.description or (a.get("content") or "").strip()
            return
        if tag == "title":
            self._in_title = True
        elif tag in _OPAQUE:
            self._opaque += 1
        self.parts.append("\n" if tag in _BLOCK else " ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _OPAQUE and self._opaque:
            self._opaque -= 1
        self.parts.append("\n" if tag in _BLOCK else " ")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._opaque:
            self.parts.append(data)


def page_text(html: str) -> tuple[str, str]:
    """(title, readable text): one paragraph per line, furniture and code dropped. A page
    built by scripts has little text, so its meta description leads when the body is thin."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = [s for line in "".join(parser.parts).split("\n") if (s := _WHITESPACE.sub(" ", line).strip())]
    if parser.description and sum(map(len, lines)) < THIN_PAGE:
        lines.insert(0, parser.description)
    return _WHITESPACE.sub(" ", parser.title).strip(), "\n".join(lines)
