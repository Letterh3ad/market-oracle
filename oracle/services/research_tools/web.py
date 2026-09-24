"""The open web: search through `ddgs`, and pages read as plain text."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen
from oracle.net.gate import SourceGate
from oracle.research import edgar
from oracle.research.news import Article
from oracle.research.tools import ToolError
from oracle.research.web import THIN_PAGE, WebResult, WebSearcher, WebSearchFailed, page_text, unsafe_url
from oracle.services.research_tools.shelf import MAX_TITLE, Shelf, Unavailable, cut, domain, fetch, query

# Fewer than news: with a snippet each, five fill an observation.
WEB_RESULTS = 5
# A page is read up to here (bytes) and shown up to MAX_PAGE_TEXT characters.
MAX_PAGE_BYTES = 2_000_000
MAX_PAGE_TEXT = 1300
# Characters of a page's text kept with its article: far more than any note call reads.
MAX_TEXT = 100_000
THIN = "too little text to read (paywalled or built by scripts)"
MAX_REDIRECTS = 5
REDIRECTS = (301, 302, 303, 307, 308)
READABLE = ("text/html", "application/xhtml+xml", "text/plain")


class WebSearch:
    """`ddgs` makes its own HTTP calls, so the gate is driven here."""

    name = "web_search"
    description = "web pages matching a query, with a snippet each (undated); input: a search query"

    def __init__(self, searcher: WebSearcher, gate: SourceGate, shelf: Shelf) -> None:
        self._searcher = searcher
        self._gate = gate
        self._shelf = shelf

    async def run(self, input: str) -> str:
        q = query(input)
        results = await search_web(self._searcher, self._gate, q)
        found = [Article(r.url, r.title or r.url, domain(r.url), None, r.snippet or None) for r in results]
        return self._shelf.show(found, "ddgs", q, WEB_RESULTS)


async def search_web(searcher: WebSearcher, gate: SourceGate, q: str) -> list[WebResult]:
    try:
        await gate.acquire("ddgs")
    except CircuitOpen as exc:
        raise Unavailable(f"web search unavailable ({exc}); try another tool") from exc
    try:
        results = await searcher.search(q, WEB_RESULTS)
    except WebSearchFailed as exc:
        if exc.rate_limited:
            gate.record("ddgs", 429)
            raise Unavailable(f"web search is rate limited ({exc}); try another tool") from exc
        gate.record_transport_error("ddgs")
        raise Unavailable(f"web search failed ({exc})") from exc
    gate.record("ddgs", 200)
    return results


@dataclass(frozen=True)
class Page:
    url: str
    """Where it was read, after any redirects."""
    title: str
    text: str
    truncated: bool
    """The page ran past its byte cap."""


async def read_page(client: RateLimitedClient, url: str, contact: str | None,
                    max_bytes: int = MAX_PAGE_BYTES) -> Page:
    """A web page as plain text, through the rate gate. Redirects are followed here, one hop
    at a time: each target is checked like the first, so a public page cannot bounce the
    reader into this machine or the LAN, and the SEC contact goes to sec.gov alone."""
    for _ in range(MAX_REDIRECTS + 1):
        resp = await _get(client, url, contact, max_bytes)
        if resp.status_code not in REDIRECTS:
            break
        if not resp.headers.get("location"):
            raise ToolError(f"the page answered HTTP {resp.status_code} without saying where to go")
        url = urljoin(url, resp.headers["location"])
    else:
        raise ToolError(f"the page redirected more than {MAX_REDIRECTS} times")
    kind = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind not in READABLE:
        raise ToolError(f"not a web page ({kind or 'unknown type'})")
    title, text = page_text(resp.text) if kind != "text/plain" else ("", resp.text.strip())
    return Page(url, title, text, resp.extensions.get("oracle_truncated", False))


async def _get(client: RateLimitedClient, url: str, contact: str | None, max_bytes: int) -> httpx.Response:
    if why := unsafe_url(url):
        raise ToolError(why)
    if domain(url) == "news.google.com":
        raise ToolError("Google News links only redirect; search its headline with web_search instead")
    # Uncompressed, so the bytes read over the wire are the bytes the cap counts.
    source, headers = "web", {"Accept-Encoding": "identity"}
    if edgar.is_sec(url):
        # SEC refuses requests without a declared contact, and has its own budget.
        source, headers = "sec_edgar", {**headers, "User-Agent": edgar.user_agent(need_contact(contact))}
    return await fetch(client, source, "the page", url, headers=headers, max_bytes=max_bytes,
                       follow_redirects=False, accept=(200, *REDIRECTS))


class ReadPage:
    """A web page as plain text, stored as evidence like an article, with its text kept so
    the reading stage does not fetch it again."""

    name = "read_page"
    description = "the text of a web page; input: its http(s) address from a search result"

    def __init__(self, client: RateLimitedClient, shelf: Shelf, contact: str | None = None) -> None:
        self._client = client
        self._shelf = shelf
        self._contact = contact

    async def run(self, input: str) -> str:
        page = await read_page(self._client, input.strip(" '\"<>"), self._contact)
        title = cut(page.title or page.url, MAX_TITLE)
        [article_id] = self._shelf.keep([Article(page.url, title, domain(page.url), None)], "web")
        if len(page.text) < THIN_PAGE:
            self._shelf.keep_unreadable(article_id, THIN)
        else:
            self._shelf.keep_text(article_id, page.url, page.text[:MAX_TEXT])
        more = len(page.text) > MAX_PAGE_TEXT or page.truncated
        body = (page.text[:MAX_PAGE_TEXT] + (" [cut]" if more else "")) or "(no readable text)"
        return f"[a{article_id}] {domain(page.url)}: {title}\n{body}"


def need_contact(contact: str | None) -> str:
    if not contact:
        raise ToolError('SEC needs a contact: set [research] contact = "Name email" in oracle.toml')
    return contact
