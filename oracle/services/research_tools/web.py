"""The open web: search through `ddgs`, and pages read as plain text."""

from __future__ import annotations

from urllib.parse import urljoin

from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen
from oracle.net.gate import SourceGate
from oracle.research import edgar
from oracle.research.news import Article
from oracle.research.tools import ToolError
from oracle.research.web import WebSearcher, WebSearchFailed, page_text, unsafe_url
from oracle.services.research_tools.shelf import MAX_TITLE, Shelf, cut, domain, fetch, query

# Fewer than news: with a snippet each, five fill an observation.
WEB_RESULTS = 5
# A page is read up to here (bytes) and shown up to MAX_PAGE_TEXT characters.
MAX_PAGE_BYTES = 2_000_000
MAX_PAGE_TEXT = 1300
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
        try:
            await self._gate.acquire("ddgs")
        except CircuitOpen as exc:
            raise ToolError(f"web search unavailable ({exc}); try another tool") from exc
        try:
            results = await self._searcher.search(q, WEB_RESULTS)
        except WebSearchFailed as exc:
            if exc.rate_limited:
                self._gate.record("ddgs", 429)
                raise ToolError(f"web search is rate limited ({exc}); try another tool") from exc
            self._gate.record_transport_error("ddgs")
            raise ToolError(f"web search failed ({exc})") from exc
        self._gate.record("ddgs", 200)
        found = [Article(r.url, r.title or r.url, domain(r.url), None, r.snippet or None) for r in results]
        return self._shelf.show(found, "ddgs", q, WEB_RESULTS)


class ReadPage:
    """A web page as plain text, stored as evidence like an article."""

    name = "read_page"
    description = "the text of a web page; input: its http(s) address from a search result"

    def __init__(self, client: RateLimitedClient, shelf: Shelf, contact: str | None = None) -> None:
        self._client = client
        self._shelf = shelf
        self._contact = contact

    async def run(self, input: str) -> str:
        url = input.strip(" '\"<>")
        # Redirects are followed here, one hop at a time: each target is checked like the
        # first, so a public page cannot bounce the reader into this machine or the LAN, and
        # the SEC contact goes to sec.gov alone.
        for _ in range(MAX_REDIRECTS + 1):
            resp = await self._get(url)
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
        title = cut(title or url, MAX_TITLE)
        [article_id] = self._shelf.keep([Article(url, title, domain(url), None)], "web")
        more = len(text) > MAX_PAGE_TEXT or resp.extensions.get("oracle_truncated", False)
        body = (text[:MAX_PAGE_TEXT] + (" [cut]" if more else "")) or "(no readable text)"
        return f"[a{article_id}] {domain(url)}: {title}\n{body}"

    async def _get(self, url: str):
        if why := unsafe_url(url):
            raise ToolError(why)
        if domain(url) == "news.google.com":
            raise ToolError("Google News links only redirect; search its headline with web_search instead")
        # Uncompressed, so the bytes read over the wire are the bytes the cap counts.
        source, headers = "web", {"Accept-Encoding": "identity"}
        if edgar.is_sec(url):
            # SEC refuses requests without a declared contact, and has its own budget.
            source, headers = "sec_edgar", {**headers, "User-Agent": edgar.user_agent(need_contact(self._contact))}
        return await fetch(self._client, source, "the page", url, headers=headers, max_bytes=MAX_PAGE_BYTES,
                           follow_redirects=False, accept=(200, *REDIRECTS))


def need_contact(contact: str | None) -> str:
    if not contact:
        raise ToolError('SEC needs a contact: set [research] contact = "Name email" in oracle.toml')
    return contact
