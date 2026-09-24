"""Web search and reading pages as text. HTML stripping adapted from the stalker's
`wfm/news/html.py`: stdlib html.parser, no dependency for what twenty lines do."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit

_WHITESPACE = re.compile(r"\s+")
# Never text worth reading, whatever is inside: code, styling, page furniture, and the hidden
# facts at the top of an inline XBRL filing.
_OPAQUE = frozenset({"script", "style", "noscript", "svg", "template", "nav", "footer", "aside", "form", "head",
                     "ix:header"})
# Characters of body text below which a page probably renders with scripts.
THIN_PAGE = 300
PRIVATE = "private and local network addresses cannot be read"
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


# Registries under a country code (bbc.co.uk, abc.net.au), where the name sits one further left:
# the common ones listed, the rest guessed from a generic label under a two-letter code.
_SECOND_LEVEL = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp", "ne.jp", "or.jp",
    "co.za", "com.br", "com.sg", "com.hk", "co.in", "co.kr", "com.cn", "com.mx", "com.tr", "co.il", "com.tw",
    "com.my", "co.id", "com.ar", "com.ph", "co.th", "com.vn", "co.ke", "com.ng", "com.pk", "com.sa", "com.eg"})
_GENERIC = frozenset({"co", "com", "net", "org", "gov", "ac", "edu", "ne", "or", "go"})
# Zones sold by private resellers, not a country's registry: anyone can have reuters.com.de.
_RESELLER = frozenset({"com.de", "co.de", "com.se", "co.nl", "com.ru", "net.ru", "org.ru"})


# Words that name no outlet, so "Yahoo Finance" is yahoo and "BBC News" is bbc.
OUTLET_FILLER = frozenset({"www", "com", "org", "net", "news", "the", "finance", "money", "markets", "business"})
_WORD = re.compile(r"[a-z0-9]+")

# Outlets whose site is registered under a name other than their own, by their name's key
# (filler dropped, so Investor's Business Daily is investorsdaily): their registered domains.
OUTLET_SITES = {
    "motleyfool": frozenset({"fool.com", "fool.co.uk", "fool.ca", "fool.com.au"}),
    "motleyfooluk": frozenset({"fool.co.uk"}), "motleyfoolcanada": frozenset({"fool.ca"}),
    "motleyfoolaustralia": frozenset({"fool.com.au"}), "wallstreetjournal": frozenset({"wsj.com"}),
    "financialtimes": frozenset({"ft.com"}), "newyorktimes": frozenset({"nytimes.com"}),
    "investorsdaily": frozenset({"investors.com"}),
}


def _name_key(outlet: str) -> str:
    words = _WORD.findall(outlet.lower())
    return "".join(w for w in words if w not in OUTLET_FILLER) or "".join(words)


def outlet_sites(outlet: str) -> frozenset[str]:
    """The registered domains of an outlet named otherwise than its site, else none."""
    return OUTLET_SITES.get(_name_key(outlet), frozenset())


def outlet_key(outlet: str) -> str:
    """A feed's outlet name as its site's registered name would read: "Reuters" is reuters,
    "BBC News" bbc, "PR Newswire" prnewswire, "The Motley Fool" fool."""
    sites = outlet_sites(outlet)
    return site_name(min(sites)) if sites else _name_key(outlet)


def registered_domain(host: str) -> str:
    """A host with its subdomains dropped: reuters.com for uk.reuters.com, bbc.co.uk for
    www.bbc.co.uk. Empty for no host."""
    labels = [label for label in host.lower().split(".") if label]
    if len(labels) < 2:
        return ""
    suffix = ".".join(labels[-2:])
    registry = suffix in _SECOND_LEVEL or (
        len(labels[-1]) == 2 and labels[-2] in _GENERIC and suffix not in _RESELLER)
    keep = 3 if len(labels) > 2 and registry else 2
    return ".".join(labels[-keep:]) if len(labels) > 1 else ""


def site_name(host: str) -> str:
    """The name a host is registered under, subdomains and suffix dropped: "reuters" for
    www.reuters.com or uk.reuters.com, "bbc" for bbc.co.uk. Empty for no host."""
    return registered_domain(host).split(".")[0]


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
    return None if ip.is_global else PRIVATE


def system_addresses(host: str) -> list[str]:
    """Every address `host` resolves to; none when it cannot be resolved."""
    try:
        found = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return [info[4][0] for info in found]


resolve = system_addresses


async def unsafe_address(url: str) -> str | None:
    """Like `unsafe_url`, by where the host resolves: a public name can point back into this
    machine (localtest.me is ::1). Checked just before each request, which narrows but cannot
    close a DNS rebind between this lookup and the connection's own. A host that does not
    resolve is left for the request itself to fail."""
    host = urlsplit(url).hostname or ""
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    found = await asyncio.to_thread(resolve, host)
    return PRIVATE if any(not ipaddress.ip_address(a).is_global for a in found) else None


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
