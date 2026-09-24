"""The reading stage: after the loop, the run reads the articles it was shown and a small
model call notes up to three facts from each. The company's own reports go first: SEC filings
for a US listing, else a results or annual report page the run's web searches found. Texts
and notes are stored, so no article is fetched or noted twice by the same model. A replay
reads filings alone: today's copy of an article may not be what was there on its day."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from urllib.parse import urlsplit

from oracle.clock import Clock
from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.research import edgar
from oracle.research.google_links import decode_offline, stub_id
from oracle.research.llm import ChatModel, ModelError
from oracle.research.news import end_of
from oracle.research.notes import take_note
from oracle.research.tools import ToolError
from oracle.research.trace import Evidence
from oracle.research.web import (
    THIN_PAGE, WebSearcher, outlet_key, outlet_sites, registered_domain, site_name, unsafe_url,
)
from oracle.services.research_tools.filings import press_exhibit
from oracle.services.research_tools.google_links import decode_online
from oracle.services.research_tools.shelf import Refused, Unavailable, domain, evidence_line
from oracle.services.research_tools.web import MAX_PAGE_BYTES, MAX_TEXT, THIN, Page, read_page, search_web
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso
from oracle.store.research import note_model

log = logging.getLogger(__name__)

UNUSABLE_NOTE = "the model's note was unusable"
# Passing, so a live run resolves it later.
REPLAY = "not resolved in a replay (a Google News link needs Google or a web search)"
# A decoded address answering these is stale; the headline search may find where it went.
GONE = (404, 410)
# Advice for the loop's model, not for someone reading why an article went unread.
TRY_ANOTHER = "; try another tool"
_WORD = re.compile(r"[a-z0-9]+")
# Reading slots the company's own reports take ahead of articles.
REPORT_SLOTS = 2
# A 10-K's main document with inline XBRL runs to several megabytes, and MD&A sits past the
# financial statements.
FILING_BYTES = 16_000_000
# Titles or addresses of a company's own results and reports, in English and some local
# editions' languages.
_REPORT = re.compile(
    r"\b(?:annual|interim|half[- ]year(?:ly)?|quarterly|integrated) report|\b(?:financial|quarterly|annual|interim|"
    r"full[- ]year|half[- ]year) results|results (?:release|announcement)|earnings release|geschaeftsbericht|"
    r"geschäftsbericht|quartalsbericht|halbjahresbericht|rapport (?:annuel|financier)|résultats|"
    r"jaarverslag|årsredovisning|årsrapport|informe anual|relazione finanziaria|決算|"
    r"有価証券報告書|실적", re.I)


class Reader:
    def __init__(self, conn: sqlite3.Connection, clock: Clock, client: RateLimitedClient,
                 searcher: WebSearcher | None, gate: SourceGate, contact: str | None, model: ChatModel,
                 company: str, context_tokens: int, note_key: str | None = None) -> None:
        """`searcher` None: Google News links are left unresolved (a replay has no web search).
        `note_key` keeps notes apart from the model's research notes, for reading asked about
        something other than one company."""
        self._articles = ArticleRepo(conn)
        self._clock = clock
        self._client = client
        self._searcher = searcher
        self._gate = gate
        self._contact = contact
        self._model = model
        self._key = note_key or note_model(model.name, model.model)
        self._company = company
        self._context = context_tokens

    async def read(self, evidence: Iterable[Evidence], limit: int, as_of: date | None,
                   progress: Callable[[int, int], None], filings: dict[int, str] | None = None) -> None:
        """Read and note up to `limit` of the articles; a failure is stored with the article
        and never raised. `filings` (id -> form, in reading order) go first; without them, the
        company's own report pages do."""
        filings = filings or {}
        # Other filings the loop listed are older or minor, and untrimmed would take article slots.
        evidence = [e for e in evidence if e.id in filings or not (as_of or edgar.is_sec(e.url))]
        state = self._articles.reading([e.id for e in evidence], self._key)
        first = list(filings) or [e.id for e in evidence if report_page(e)]
        todo = pick(evidence, {i for i, r in state.items() if r.settled}, limit, as_of, first)
        for n, e in enumerate(todo, 1):
            progress(n, len(todo))
            form = filings.get(e.id)
            # A filing the loop read is stored whole, not trimmed, so it is fetched again.
            text = (None if form else state[e.id].text) or await self._fetch(e, state[e.id].site, form)
            if text:
                await self._note(e, text)

    async def _fetch(self, e: Evidence, site: str | None, form: str | None) -> str | None:
        try:
            page = await (self._read_stub(e, site) if domain(e.url) == "news.google.com" else
                          read_page(self._client, e.url, self._contact, FILING_BYTES if form else MAX_PAGE_BYTES))
            if len(page.text) < THIN_PAGE:
                raise ToolError(THIN)
            read_at, text = await self._filing(form, page) if form else (page.url, page.text[:MAX_TEXT])
        except ToolError as exc:
            why = str(exc).removesuffix(TRY_ANOTHER)
            self._articles.save_read_failure(e.id, why, isinstance(exc, Unavailable), self._now())
            return None
        # One article must never cost the run, whatever breaks on it.
        except Exception as exc:
            log.exception("reading article %s failed", e.id)
            self._articles.save_read_failure(e.id, unexpected(exc), False, self._now())
            return None
        self._articles.save_text(e.id, read_at, text, self._now())
        return text

    async def _filing(self, form: str, page: Page) -> tuple[str, str]:
        """Where a filing's key text was read, and the text: an earnings or press 8-K's from
        its press exhibit, which its own items only point at. SEC trouble is raised, so a
        later run reads the exhibit; an exhibit that is missing or unreadable for good leaves
        the 8-K's own items, which still say what happened."""
        if form == edgar.EVENT and edgar.reads_exhibit(page.text):
            try:
                exhibit = await press_exhibit(self._client, self._contact, page.url)
                if exhibit:
                    read = await read_page(self._client, exhibit.url, self._contact, FILING_BYTES)
                    if len(read.text) >= THIN_PAGE:
                        return read.url, edgar.with_exhibit(exhibit, read.text, page.text)
            except Unavailable:
                raise
            except ToolError:
                log.info("reading the press exhibit of %s failed", page.url, exc_info=True)
        return page.url, edgar.key_text(form, page.text)

    async def _read_stub(self, e: Evidence, site: str | None) -> Page:
        """The article behind a Google News redirect stub: at its decoded address, else (or
        when that is gone) the first result at the outlet's own site of a search for its headline."""
        decoded, why, passing = await self._decode(e, site)
        if decoded:
            try:
                return await read_page(self._client, decoded, self._contact)
            except Refused as exc:
                if exc.status not in GONE:
                    raise
                why = f"{decoded} is gone ({exc})"
        return await read_page(self._client, await self._search(e, site, why, passing), self._contact)

    async def _decode(self, e: Evidence, site: str | None) -> tuple[str | None, str, bool]:
        """The stub's address if it passes the outlet check, else why not and whether that
        failure passes. An older stub carries it; a newer one needs Google's decoder."""
        stub = stub_id(e.url)
        if stub is None:
            return None, "the link has no id to decode", False
        url = decode_offline(stub)
        if url is None:
            if self._searcher is None:
                raise Unavailable(REPLAY)
            try:
                url = await decode_online(self._client, stub)
            except ToolError as exc:
                return None, str(exc).removesuffix(TRY_ANOTHER), isinstance(exc, Unavailable)
        why = off_outlet(url, site, e.outlet)
        return (None, why, False) if why else (url, "", False)

    async def _search(self, e: Evidence, site: str | None, why: str, passing: bool) -> str:
        """The first result at the outlet's own site of a web search for the headline."""
        if self._searcher is None:
            raise Unavailable(REPLAY)
        failed = f"could not resolve the Google News link: {why}"
        try:
            results = await search_web(self._searcher, self._gate, e.title)
        except Unavailable as exc:
            raise Unavailable(f"{failed}, and {exc}") from exc
        for r in results:
            if from_outlet(r.url, site, e.outlet):
                return r.url
        failure = f"{failed}, and no search result from {site or e.outlet or 'its outlet'}"
        raise Unavailable(failure) if passing else ToolError(failure)

    async def _note(self, e: Evidence, text: str) -> None:
        heading = evidence_line(e.id, e.published, e.outlet, e.title)
        try:
            note = await take_note(self._model, self._company, heading, text, self._context)
        except ModelError as exc:
            self._articles.save_note(e.id, self._key, None, f"the model could not note it: {exc}", self._now(),
                                     attempt=False)
            return
        except Exception as exc:
            log.exception("noting article %s failed", e.id)
            self._articles.save_note(e.id, self._key, None, unexpected(exc), self._now())
            return
        self._articles.save_note(e.id, self._key, note, None if note else UNUSABLE_NOTE, self._now())

    def _now(self) -> str:
        return to_utc_iso(self._clock.utcnow())


def pick(evidence: Iterable[Evidence], done: set[int], limit: int, as_of: date | None = None,
         first: Sequence[int] = ()) -> list[Evidence]:
    """Up to `limit` articles to read, from evidence listed newest first: up to REPORT_SLOTS of
    `first` in its order (the rest of `first` not at all), then one per outlet before any outlet's second, skipping stored data,
    what is done, and in a replay anything not dated before its end."""
    cutoff = end_of(as_of) if as_of else None
    todo = [e for e in evidence if e.body is None and e.id not in done
            and (cutoff is None or (e.published is not None and e.published < cutoff))]
    by_id = {e.id: e for e in todo}
    reports = [by_id[i] for i in first if i in by_id][:REPORT_SLOTS]
    seen: Counter[str] = Counter()
    rank = {}
    for e in todo:
        outlet = (e.outlet or domain(e.url)).lower()
        rank[e.id] = seen[outlet]
        seen[outlet] += 1
    rest = sorted((e for e in todo if e.id not in first), key=lambda e: rank[e.id])
    return (reports + rest)[:limit]


def report_page(e: Evidence) -> bool:
    """Whether a web search result looks like the company's own results or report page. Web
    results are the undated evidence; a PDF cannot be read."""
    path = urlsplit(e.url).path.lower()
    return (e.published is None and e.body is None and not path.endswith(".pdf")
            and bool(_REPORT.search(f"{e.title} {path}")))


def from_outlet(url: str, site: str | None, outlet: str | None) -> bool:
    """Whether an address is on the outlet's own site: its domain when the feed named one
    (or any host of that registered domain, when it is registered under the outlet's name),
    else a domain whose own name is the outlet's ("BBC" and bbc.co.uk, "The Motley Fool" and
    fool.com). The whole name, so reutersnews-daily.com or bbc.fakenews.com is never taken
    for the outlet."""
    host = domain(url)
    key = outlet_key(outlet or "")
    if site:
        # A shared host (jane.substack.com) is registered under no outlet's name, so stays exact.
        same = key and site_name(site) == key and registered_domain(host) == registered_domain(site)
        return host == site or host.endswith("." + site) or bool(same)
    if listed := outlet_sites(outlet or ""):
        return registered_domain(host) in listed
    name = site_name(host)
    names = {"".join(_WORD.findall((outlet or "").lower())), key}
    return len(name) >= 3 and name in names


def off_outlet(url: str, site: str | None, outlet: str | None) -> str | None:
    """Why a decoded address must not be read, or None."""
    if why := unsafe_url(url):
        return f"{url} is not readable ({why})"
    if not from_outlet(url, site, outlet):
        return f"{url} is not at {site or outlet or 'its outlet'}"
    return None


def unexpected(exc: Exception) -> str:
    return f"unexpected error: {type(exc).__name__}: {exc}"
