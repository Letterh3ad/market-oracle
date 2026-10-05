"""Full texts for the literature index (research-lab 07, spec decision 4). A source with a DOI (or an SSRN,
arXiv or NBER id) is looked up on Unpaywall, then Semantic Scholar; a legal open copy is downloaded, its text
kept in the research database and the source marked full-text. No copy: the source goes on "papers I'd like"
for the user to fetch through their library and drop in the inbox folder, where a scan matches each PDF to its
source by id, else by title, and keeps its text. Shadow libraries are never used."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

from oracle.lab.matching import normal_title, source_key, title_score
from oracle.lab.papers import (
    Copy, NotAPdf, PdfText, api_ids, ids_in, is_shadow, pdf_text, s2_copies, title_candidates, unpaywall_copies,
)
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research.tools import ToolError
from oracle.research.web import unsafe_address, unsafe_url
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.http import open_client
from oracle.services.literature_service import Source, open_index, sources
from oracle.services.research_tools.web import follow
from oracle.store.db import to_utc_iso
from oracle.store.literature import LiteratureRepo, SourceRow
from oracle.store.papers import FullTextRow, PapersRepo

__all__ = ["Fetched", "FullText", "Held", "Inbox", "InboxResult", "Kept", "Wanted", "fetch", "full_text",
           "inbox_folder", "scan_inbox", "text", "texts", "unwant", "want", "wanted"]

UNPAYWALL = "https://api.unpaywall.org/v2/{}"
S2 = "https://api.semanticscholar.org/graph/v1/paper/{}"
S2_FIELDS = "title,externalIds,isOpenAccess,openAccessPdf"
PDF_BYTES = 40_000_000
MIN_CHARS = 1000
"""Fewer characters than this is an abstract page or a scan without a text layer, not a full text."""
TITLE_FLOOR = 0.9
ID_TITLE_FLOOR = 0.6
"""An id found only in a PDF's pages may be one it cites, so its title must roughly agree too; an unrelated
title scores about 0.4 against a recorded one (shared words like returns, stock)."""
TITLE_MARGIN = 0.03
"""A title match must beat the next source by this much, or the PDF is left for the user."""
MAIN_TITLE_WORDS = 3
"""A title's part before its colon is matched alone only when it has this many words."""
DONE = "done"
"""Inbox subfolder ingested PDFs move to, so a scan never reads them twice."""
EMAIL_SETTING = "set [research] open_access_email in Settings"
_ID_IN_NAME = re.compile(r"^(10\.\d{4,9})_")


@dataclass(frozen=True)
class FullText:
    via: str
    url: str | None
    licence: str
    version: str
    file: str | None
    pages: int
    chars: int
    at: str
    by: str


@dataclass(frozen=True)
class Held:
    source: Source
    full_text: FullText


@dataclass(frozen=True)
class Kept(Held):
    text: str


@dataclass(frozen=True)
class Fetched:
    source: Source
    copy: Copy | None
    """The open copy whose text was kept; None when none was found."""
    pages: int
    chars: int
    wanted: bool
    """Put on (or left on) papers I'd like; False also when a lookup did not answer, so no copy is known."""
    note: str
    """What each place answered."""


@dataclass(frozen=True)
class Wanted:
    source: Source
    reason: str
    note: str
    at: str
    by: str


@dataclass(frozen=True)
class InboxResult:
    file: str
    source: Source | None
    matched: str
    """"id <key>" or "title <score>"; "" when unmatched."""
    pages: int
    chars: int
    note: str


@dataclass(frozen=True)
class Inbox:
    folder: str
    results: list[InboxResult]


@contextmanager
def _repos(ctx: AppContext) -> Iterator[tuple[LiteratureRepo, PapersRepo]]:
    with open_index(ctx) as r:
        yield LiteratureRepo(r.conn), PapersRepo(r.conn)


def _by(by: str) -> str:
    by = by.strip()
    if not by or len(by) > 40:
        raise InvalidQuery("by names who did it (1 to 40 characters), e.g. user or autopilot")
    return by


def _resolve(repo: LiteratureRepo, ident: str) -> SourceRow:
    """A source by any of its ids or URL, or by its number (12 or #12)."""
    raw = ident.strip()
    found = None
    if raw.lstrip("#").isdigit():
        found = repo.source(int(raw.lstrip("#")))
    elif key := source_key(raw):
        found = repo.source_by_key(key)
    if found is None:
        raise InvalidQuery(f"{raw!r} is not a recorded source: record it first (oracle lab record, or Record a "
                           "source on the Research tab)")
    return found


def _source(ctx: AppContext, source_id: int) -> Source:
    return next(s for s in sources(ctx) if s.id == source_id)


def _full(row: FullTextRow | None) -> FullText | None:
    return None if row is None else FullText(row.via, row.url, row.licence, row.version, row.file, row.pages,
                                             row.chars, row.at, row.by)


def _origin(held: FullTextRow) -> str:
    return f"from {held.via}, {held.at[:10]}"


def full_text(ctx: AppContext, ident: str) -> FullText | None:
    with _repos(ctx) as (lit, papers):
        return _full(papers.full_text(_resolve(lit, ident).id))


def texts(ctx: AppContext) -> list[Held]:
    """Every source with a full text, by number."""
    with _repos(ctx) as (_, papers):
        rows = papers.full_texts()
    if not rows:
        return []
    by_id = {s.id: s for s in sources(ctx)}
    return [Held(by_id[r.source_id], _full(r)) for r in rows]


def text(ctx: AppContext, ident: str) -> Kept:
    with _repos(ctx) as (lit, papers):
        s = _resolve(lit, ident)
        held, body = papers.full_text(s.id), papers.text(s.id)
    if held is None or body is None:
        raise InvalidQuery(f"#{s.id} {s.title!r} has no full text yet: fetch an open copy (oracle lab fetch, or "
                           "Fetch open copy on the Research tab), or drop its PDF in the inbox and scan it")
    return Kept(_source(ctx, s.id), _full(held), body)


def wanted(ctx: AppContext) -> list[Wanted]:
    """Papers I'd like, oldest first."""
    with _repos(ctx) as (_, papers):
        rows = papers.wanted()
    if not rows:
        return []
    by_id = {s.id: s for s in sources(ctx)}
    return [Wanted(by_id[w.source_id], w.reason, w.note, w.at, w.by) for w in rows]


def want(ctx: AppContext, ident: str, note: str = "", by: str = "user", reason: str = "added by hand") -> Wanted:
    by = _by(by)
    with _repos(ctx) as (lit, papers):
        s = _resolve(lit, ident)
        if (held := papers.full_text(s.id)) is not None:
            raise InvalidQuery(f"#{s.id} {s.title!r} already has its full text ({_origin(held)}): nothing to want")
        papers.want(s.id, reason, note.strip(), to_utc_iso(ctx.clock.utcnow()), by)
    return next(w for w in wanted(ctx) if w.source.id == s.id)


def unwant(ctx: AppContext, ident: str) -> None:
    with _repos(ctx) as (lit, papers):
        s = _resolve(lit, ident)
        if not papers.unwant(s.id):
            raise InvalidQuery(f"#{s.id} {s.title!r} is not on the list of papers I'd like")


async def fetch(ctx: AppContext, ident: str, by: str = "user") -> Fetched:
    """One source's open-access lookup: a few API calls and at most a few downloads."""
    by = _by(by)
    with _repos(ctx) as (lit, papers):
        s = _resolve(lit, ident)
        keys = lit.keys(s.id)
        held = papers.full_text(s.id)
    if held is not None:
        return Fetched(_source(ctx, s.id), None, held.pages, held.chars, False,
                       f"already full-text ({_origin(held)}): nothing fetched")
    ids = next((api_ids(k) for k in keys if api_ids(k)[0]), None)
    if ids is None:
        raise InvalidQuery(f"#{s.id} {s.title!r} has no DOI, SSRN, arXiv or NBER id, so there is nothing to look up; "
                           "add it to papers I'd like instead")
    doi, s2_id = ids
    notes: list[str] = []
    answered = True
    email = ctx.config.research.open_access_email
    async with open_client(ctx) as client:
        got = None
        if email is None:
            notes.append(f"Unpaywall: skipped ({EMAIL_SETTING})")
        else:
            body, ok = await _ask(client, "unpaywall", "Unpaywall", UNPAYWALL.format(quote(doi, safe="/")),
                                  {"email": email}, notes)
            answered &= ok
            if body is not None:
                copies = unpaywall_copies(body)
                if not copies:
                    landing = body.get("best_oa_location") or body.get("oa_locations")
                    notes.append(f"Unpaywall: {body.get('oa_status') or 'no'} "
                                 + ("(its open copy is a landing page only)" if landing else "(no open copy)"))
                got = await _first_readable(client, copies, notes)
        if got is None:
            body, ok = await _ask(client, "semantic_scholar", "Semantic Scholar",
                                  S2.format(quote(s2_id, safe=":/")), {"fields": S2_FIELDS}, notes)
            answered &= ok
            if body is not None:
                copies = s2_copies(body)
                if not copies:
                    closed = (body.get("openAccessPdf") or {}).get("url")
                    notes.append("Semantic Scholar: no open copy" + (" (its only link is marked closed)" if closed else ""))
                got = await _first_readable(client, copies, notes)
    if got is None and not answered:
        notes.append("not put on papers I'd like, as a lookup did not answer: try again later")
    note = "; ".join(notes)
    at = to_utc_iso(ctx.clock.utcnow())
    with _repos(ctx) as (_, papers):
        if got is None:
            if answered:
                papers.want(s.id, note, "", at, by)
        else:
            copy, pdf = got
            papers.keep_text(s.id, pdf.text, copy.via, copy.url, copy.licence, copy.version, None, len(pdf.pages), at, by)
    source = _source(ctx, s.id)
    if got is None:
        return Fetched(source, None, 0, 0, answered, note)
    return Fetched(source, got[0], len(got[1].pages), len(got[1].text), False, note)


async def _ask(client, source: str, label: str, url: str, params: dict,
               notes: list[str]) -> tuple[dict | None, bool]:
    """The answer's body, and whether the place answered at all (a 404 is an answer: it has no record)."""
    try:
        r = await client.get(source, url, params=params, count_403=False)
    except (CircuitOpen, RequestFailed) as exc:
        notes.append(f"{label}: unreachable ({exc})")
        return None, False
    if r.status_code == 404:
        notes.append(f"{label}: not known")
        return None, True
    if r.status_code != 200:
        notes.append(f"{label}: HTTP {r.status_code}")
        return None, False
    try:
        body = r.json()
    except ValueError:
        notes.append(f"{label}: not JSON")
        return None, False
    return (body, True) if isinstance(body, dict) else (None, False)


async def _first_readable(client, copies: list[Copy], notes: list[str]) -> tuple[Copy, PdfText] | None:
    for c in copies:
        try:
            final, r = await follow(c.url, lambda url, first=c.url: _download(client, url, first), "the copy")
        except ToolError as exc:
            notes.append(f"{c.url}: {exc}")
            continue
        except (CircuitOpen, RequestFailed) as exc:
            notes.append(f"{c.url}: unreachable ({exc})")
            continue
        if r.status_code != 200:
            notes.append(f"{c.url}: HTTP {r.status_code}")
            continue
        if r.extensions.get("oracle_truncated"):
            notes.append(f"{c.url}: over {PDF_BYTES // 1_000_000} MB")
            continue
        try:
            pdf = await asyncio.to_thread(pdf_text, r.content)
        except NotAPdf as exc:
            notes.append(f"{c.url}: {exc}")
            continue
        if len(pdf.text.strip()) < MIN_CHARS:
            notes.append(f"{c.url}: too little text ({len(pdf.text.strip())} characters)")
            continue
        notes.append(f"{c.via}: read {final}" + (f" (redirected from {c.url})" if final != c.url else ""))
        return replace(c, url=final), pdf
    return None


async def _download(client, url: str, first: str):
    """One hop of a copy's download, each target checked as the first is, so an open repository cannot redirect
    the download to a shadow library or into this machine or the LAN."""
    why = "a shadow library" if is_shadow(url) else unsafe_url(url) or await unsafe_address(url)
    if why:
        raise ToolError(f"refused ({why})" if url == first else f"redirected to {url}, refused ({why})")
    return await client.get("web", url, max_bytes=PDF_BYTES, count_403=False, breaker=False, follow_redirects=False)


def inbox_folder(ctx: AppContext) -> Path:
    path = Path(ctx.config.research.inbox)
    if path.is_absolute():
        return path
    return (ctx.config_source.parent if ctx.config_source else Path.cwd()) / path


def scan_inbox(ctx: AppContext, by: str = "user") -> Inbox:
    """Every PDF in the inbox matched by an id in its name or first pages, else by its title; a match's text is
    kept and the file moves to done/. The rest stay for the user."""
    by = _by(by)
    folder = inbox_folder(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    with _repos(ctx) as (lit, papers):
        rows = lit.sources()
        for path in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"):
            results.append(_ingest(ctx, lit, papers, rows, path, folder, by))
    by_id = {s.id: s for s in sources(ctx)} if any(r[1] is not None for r in results) else {}
    return Inbox(str(folder), [InboxResult(f, by_id.get(sid) if sid else None, m, p, c, n)
                               for f, sid, m, p, c, n in results])


def _ingest(ctx, lit: LiteratureRepo, papers: PapersRepo, rows: list[SourceRow], path: Path, folder: Path, by: str):
    name = path.name
    try:
        pdf = pdf_text(path.read_bytes())
    except (NotAPdf, OSError) as exc:
        return name, None, "", 0, 0, f"cannot be read: {exc}"
    if not pdf.text.strip():
        return name, None, "", len(pdf.pages), 0, "has no text layer (a scanned image?): it cannot be read without OCR"
    found, how, from_page_text = _match(lit, rows, pdf, name)
    if found is None:
        return name, None, "", len(pdf.pages), len(pdf.text), how
    earlier = papers.full_text(found.id)
    if from_page_text and earlier is not None:
        return (name, None, "", len(pdf.pages), len(pdf.text),
                f"names #{found.id} only in its text and #{found.id} already has a full text ({_origin(earlier)}), so "
                "it is left; rename it to the DOI to replace that text")
    done = folder / DONE
    done.mkdir(exist_ok=True)
    target = done / name
    if target.exists():
        target = done / f"{path.stem}-{ctx.clock.utcnow():%Y%m%d%H%M%S}{path.suffix}"
    papers.keep_text(found.id, pdf.text, "inbox", None, "", "", str(target), len(pdf.pages),
                     to_utc_iso(ctx.clock.utcnow()), by)
    note = f"kept as the full text of #{found.id}"
    if earlier is not None:
        note += f"; it replaces its earlier full text ({_origin(earlier)})"
    try:
        shutil.move(path, target)
    except OSError as exc:
        note += f"; could not move it to {DONE}/ ({exc}), so the next scan reads it again"
    return name, found.id, how, len(pdf.pages), len(pdf.text), note


def _match(lit: LiteratureRepo, rows: list[SourceRow], pdf: PdfText,
           name: str) -> tuple[SourceRow | None, str, bool]:
    """An id in the file name or the title field settles it. One found only in the first two pages may be one the
    paper cites, so its title must roughly agree too, and beat every other recorded source's (a follow-up paper
    titled like the one it cites is recorded in its own right). The flag says the id came from page text. Else by
    title alone. An id naming two recorded sources is
    settled by title among them."""
    candidates = title_candidates(pdf, name)

    def scored(pool: list[SourceRow]) -> list[tuple[float, SourceRow]]:
        return sorted(((max((title_score(c, t) for c in candidates for t in _titles(s)), default=0.0), s)
                       for s in pool), key=lambda x: -x[0])

    named = _recorded(lit, "\n".join((_ID_IN_NAME.sub(r"\1/", Path(name).stem), pdf.title or "")))
    if len(named) == 1:
        return named[0][0], f"id {named[0][1]}", False
    if named:
        return (*_by_title(scored([s for s, _ in named])), False)
    cited = _recorded(lit, "\n".join(pdf.pages[:2]))
    keys = {s.id: key for s, key in cited}
    found = scored([s for s, _ in cited])
    overall = scored(rows)
    if found and found[0][0] >= ID_TITLE_FLOOR:
        rivals = [score for score, s in overall if s.id != found[0][1].id]
        if not rivals or found[0][0] - max(rivals) >= TITLE_MARGIN:
            return found[0][1], f"id {keys[found[0][1].id]} (title {found[0][0]:.2f})", True
    s, how = _by_title(overall)
    if s is None and cited:
        named_ids = ", ".join(f"#{x.id}" for _, x in found)
        how = (f"it names {named_ids} in its text but its title does not match (best {found[0][0]:.2f}), so it may "
               f"only cite {'it' if len(found) == 1 else 'them'}; rename it to its DOI if it is that paper")
    return s, how, False


def _recorded(lit: LiteratureRepo, text: str) -> list[tuple[SourceRow, str]]:
    """Recorded sources the ids in `text` name, once each, with the key that named them."""
    hits: dict[int, tuple[SourceRow, str]] = {}
    for key in ids_in(text):
        s = lit.source_by_key(key)
        if s is not None and s.id not in hits:
            hits[s.id] = (s, key)
    return list(hits.values())


def _by_title(scored: list[tuple[float, SourceRow]]) -> tuple[SourceRow | None, str]:
    if not scored:
        return None, "no recorded sources"
    if scored[0][0] < TITLE_FLOOR:
        return None, (f"no recorded source matches (best title score {scored[0][0]:.2f}); record it, or rename the "
                      "file to its DOI or title")
    if len(scored) > 1 and scored[0][0] - scored[1][0] < TITLE_MARGIN:
        return None, f"two sources match its title equally (#{scored[0][1].id}, #{scored[1][1].id}); rename it to its DOI"
    return scored[0][1], f"title {scored[0][0]:.2f}"


def _titles(s: SourceRow) -> list[str]:
    out = [t for t in (s.title, s.also_titled) if t]
    for t in list(out):
        main = t.split(":")[0]
        if main != t and len(normal_title(main).split()) >= MAIN_TITLE_WORDS:
            out.append(main)
    return out
