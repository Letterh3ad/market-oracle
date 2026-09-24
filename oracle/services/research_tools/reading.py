"""The reading stage: after the loop, the run reads the articles it was shown and a small
model call notes up to three facts from each. Texts and notes are stored, so no article is
fetched or noted twice by the same model."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date

from oracle.clock import Clock
from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.research.llm import ChatModel, ModelError
from oracle.research.news import end_of
from oracle.research.notes import take_note
from oracle.research.tools import ToolError
from oracle.research.trace import Evidence
from oracle.research.web import THIN_PAGE, WebSearcher
from oracle.services.research_tools.shelf import Unavailable, domain, evidence_line
from oracle.services.research_tools.web import MAX_TEXT, THIN, read_page, search_web
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso
from oracle.store.research import note_model

UNUSABLE_NOTE = "the model's note was unusable"
# Advice for the loop's model, not for someone reading why an article went unread.
TRY_ANOTHER = "; try another tool"
# Domain labels that name no outlet, so an outlet's name is never matched on them.
_GENERIC = frozenset({"www", "com", "org", "net", "news", "the", "finance", "money", "markets", "business"})
_NOT_NAME = re.compile(r"[^a-z0-9]")


class Reader:
    def __init__(self, conn: sqlite3.Connection, clock: Clock, client: RateLimitedClient,
                 searcher: WebSearcher | None, gate: SourceGate, contact: str | None, model: ChatModel,
                 company: str, context_tokens: int) -> None:
        """`searcher` None: Google News links are left unresolved (a replay has no web search)."""
        self._articles = ArticleRepo(conn)
        self._clock = clock
        self._client = client
        self._searcher = searcher
        self._gate = gate
        self._contact = contact
        self._model = model
        self._key = note_model(model.name, model.model)
        self._company = company
        self._context = context_tokens

    async def read(self, evidence: Iterable[Evidence], limit: int, as_of: date | None,
                   progress: Callable[[int, int], None]) -> None:
        """Read and note up to `limit` of the articles; a failure is stored with the article
        and never raised."""
        evidence = list(evidence)
        state = self._articles.reading([e.id for e in evidence], self._key)
        todo = pick(evidence, {i for i, r in state.items() if r.settled}, limit, as_of)
        for n, e in enumerate(todo, 1):
            progress(n, len(todo))
            text = state[e.id].text or await self._fetch(e, state[e.id].site)
            if text:
                await self._note(e, text)

    async def _fetch(self, e: Evidence, site: str | None) -> str | None:
        try:
            url = await self._resolve(e, site) if domain(e.url) == "news.google.com" else e.url
            page = await read_page(self._client, url, self._contact)
            if len(page.text) < THIN_PAGE:
                raise ToolError(THIN)
        except ToolError as exc:
            why = str(exc).removesuffix(TRY_ANOTHER)
            self._articles.save_read_failure(e.id, why, isinstance(exc, Unavailable), self._now())
            return None
        text = page.text[:MAX_TEXT]
        self._articles.save_text(e.id, page.url, text, self._now())
        return text

    async def _resolve(self, e: Evidence, site: str | None) -> str:
        """The article behind a Google News redirect stub: a web search for its headline,
        taking the first result at the outlet's own site."""
        if self._searcher is None:
            # Passing, so a live run resolves it later.
            raise Unavailable("not resolved in a replay (a Google News link needs a web search)")
        why = "could not resolve the Google News link"
        try:
            results = await search_web(self._searcher, self._gate, e.title)
        except Unavailable as exc:
            raise Unavailable(f"{why}: {exc}") from exc
        for r in results:
            if from_outlet(r.url, site, e.outlet):
                return r.url
        raise ToolError(f"{why}: no search result from {site or e.outlet or 'its outlet'}")

    async def _note(self, e: Evidence, text: str) -> None:
        heading = evidence_line(e.id, e.published, e.outlet, e.title)
        try:
            note = await take_note(self._model, self._company, heading, text, self._context)
        except ModelError as exc:
            self._articles.save_note(e.id, self._key, None, f"the model could not note it: {exc}", self._now())
            return
        self._articles.save_note(e.id, self._key, note, None if note else UNUSABLE_NOTE, self._now())

    def _now(self) -> str:
        return to_utc_iso(self._clock.utcnow())


def pick(evidence: Iterable[Evidence], done: set[int], limit: int, as_of: date | None = None) -> list[Evidence]:
    """Up to `limit` articles to read, from evidence listed newest first: one per outlet before
    any outlet's second, skipping stored data, what is done, and in a replay anything not
    dated before its end."""
    cutoff = end_of(as_of) if as_of else None
    todo = [e for e in evidence if e.body is None and e.id not in done
            and (cutoff is None or (e.published is not None and e.published < cutoff))]
    seen: Counter[str] = Counter()
    rank = {}
    for e in todo:
        outlet = (e.outlet or domain(e.url)).lower()
        rank[e.id] = seen[outlet]
        seen[outlet] += 1
    return sorted(todo, key=lambda e: rank[e.id])[:limit]


def from_outlet(url: str, site: str | None, outlet: str | None) -> bool:
    """Whether a search result is on the outlet's own site: its domain when the feed named
    one, else a domain label matching the outlet's name ("BBC" and bbc.co.uk)."""
    host = domain(url)
    if site:
        return host == site or host.endswith("." + site)
    name = _NOT_NAME.sub("", (outlet or "").lower())
    labels = [part for part in host.split(".")[:-1] if len(part) >= 3 and part not in _GENERIC]
    return len(name) >= 3 and any(part in name or name in part for part in labels)
