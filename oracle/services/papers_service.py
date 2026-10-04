"""Full texts for the literature index (research-lab 07, spec decision 4). A source with a DOI (or an SSRN,
arXiv or NBER id) is looked up on Unpaywall, then Semantic Scholar; a legal open copy is downloaded, its text
kept in the research database and the source marked full-text. No copy: the source goes on "papers I'd like"
for the user to fetch through their library and drop in the inbox folder, where a scan matches each PDF to its
source by id, else by title, and keeps its text. Shadow libraries are never used."""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from oracle.lab.matching import normal_title, source_key, title_score
from oracle.lab.papers import (
    Copy, NotAPdf, PdfText, api_ids, ids_in, is_shadow, pdf_text, s2_copies, title_candidates, unpaywall_copies,
)
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research.web import unsafe_address, unsafe_url
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.http import open_client
from oracle.services.literature_service import Source, open_index, sources
from oracle.store.db import to_utc_iso
from oracle.store.literature import LiteratureRepo, SourceRow
from oracle.store.papers import FullTextRow, PapersRepo

__all__ = ["Fetched", "FullText", "Inbox", "InboxResult", "Wanted", "fetch", "full_text", "inbox_folder",
           "scan_inbox", "text", "unwant", "want", "wanted"]

UNPAYWALL = "https://api.unpaywall.org/v2/{}"
S2 = "https://api.semanticscholar.org/graph/v1/paper/{}"
S2_FIELDS = "title,externalIds,isOpenAccess,openAccessPdf"
PDF_BYTES = 40_000_000
MIN_CHARS = 1000
"""Fewer characters than this is an abstract page or a scan without a text layer, not a full text."""
TITLE_FLOOR = 0.9
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
class Fetched:
    source: Source
    copy: Copy | None
    """The open copy whose text was kept; None when none was found."""
    pages: int
    chars: int
    wanted: bool
    """Put on (or left on) papers I'd like."""
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


def full_text(ctx: AppContext, ident: str) -> FullText | None:
    with _repos(ctx) as (lit, papers):
        return _full(papers.full_text(_resolve(lit, ident).id))


def text(ctx: AppContext, ident: str) -> str | None:
    with _repos(ctx) as (lit, papers):
        return papers.text(_resolve(lit, ident).id)


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
                       f"already full-text (from {held.via}, {held.at[:10]}): nothing fetched")
    ids = next((api_ids(k) for k in keys if api_ids(k)[0]), None)
    if ids is None:
        raise InvalidQuery(f"#{s.id} {s.title!r} has no DOI, SSRN, arXiv or NBER id, so there is nothing to look up; "
                           "add it to papers I'd like instead")
    doi, s2_id = ids
    notes: list[str] = []
    email = ctx.config.research.open_access_email
    async with open_client(ctx) as client:
        got = None
        if email is None:
            notes.append(f"Unpaywall: skipped ({EMAIL_SETTING})")
        else:
            body = await _ask(client, "unpaywall", "Unpaywall", UNPAYWALL.format(quote(doi, safe="/")),
                              {"email": email}, notes)
            if body is not None:
                copies = unpaywall_copies(body)
                if not copies:
                    notes.append(f"Unpaywall: {body.get('oa_status') or 'no'} (no open copy)")
                got = await _first_readable(client, copies, notes)
        if got is None:
            body = await _ask(client, "semantic_scholar", "Semantic Scholar", S2.format(quote(s2_id, safe=":/")),
                              {"fields": S2_FIELDS}, notes)
            if body is not None:
                copies = s2_copies(body)
                if not copies:
                    closed = (body.get("openAccessPdf") or {}).get("url")
                    notes.append("Semantic Scholar: no open copy" + (" (its only link is marked closed)" if closed else ""))
                got = await _first_readable(client, copies, notes)
    note = "; ".join(notes)
    at = to_utc_iso(ctx.clock.utcnow())
    with _repos(ctx) as (_, papers):
        if got is None:
            papers.want(s.id, note, "", at, by)
        else:
            copy, pdf = got
            papers.keep_text(s.id, pdf.text, copy.via, copy.url, copy.licence, copy.version, None, len(pdf.pages), at, by)
    source = _source(ctx, s.id)
    if got is None:
        return Fetched(source, None, 0, 0, True, note)
    return Fetched(source, got[0], len(got[1].pages), len(got[1].text), False, note)


async def _ask(client, source: str, label: str, url: str, params: dict, notes: list[str]) -> dict | None:
    try:
        r = await client.get(source, url, params=params, count_403=False)
    except (CircuitOpen, RequestFailed) as exc:
        notes.append(f"{label}: unreachable ({exc})")
        return None
    if r.status_code == 404:
        notes.append(f"{label}: not known")
        return None
    if r.status_code != 200:
        notes.append(f"{label}: HTTP {r.status_code}")
        return None
    try:
        body = r.json()
    except ValueError:
        notes.append(f"{label}: not JSON")
        return None
    return body if isinstance(body, dict) else None


async def _first_readable(client, copies: list[Copy], notes: list[str]) -> tuple[Copy, PdfText] | None:
    for c in copies:
        if is_shadow(c.url) or (why := unsafe_url(c.url) or await unsafe_address(c.url)):
            notes.append(f"{c.url}: refused ({'a shadow library' if is_shadow(c.url) else why})")
            continue
        try:
            r = await client.get("web", c.url, max_bytes=PDF_BYTES, count_403=False, breaker=False)
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
            pdf = pdf_text(r.content)
        except NotAPdf as exc:
            notes.append(f"{c.url}: {exc}")
            continue
        if len(pdf.text.strip()) < MIN_CHARS:
            notes.append(f"{c.url}: too little text ({len(pdf.text.strip())} characters)")
            continue
        notes.append(f"{c.via}: read {c.url}")
        return c, pdf
    return None


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
    found, how = _match(lit, rows, pdf, name)
    if found is None:
        return name, None, "", len(pdf.pages), len(pdf.text), how
    done = folder / DONE
    done.mkdir(exist_ok=True)
    target = done / name
    if target.exists():
        target = done / f"{path.stem}-{ctx.clock.utcnow():%Y%m%d%H%M%S}{path.suffix}"
    papers.keep_text(found.id, pdf.text, "inbox", None, "", "", str(target), len(pdf.pages),
                     to_utc_iso(ctx.clock.utcnow()), by)
    note = f"kept as the full text of #{found.id}"
    try:
        shutil.move(path, target)
    except OSError as exc:
        note += f"; could not move it to {DONE}/ ({exc}), so the next scan reads it again"
    return name, found.id, how, len(pdf.pages), len(pdf.text), note


def _match(lit: LiteratureRepo, rows: list[SourceRow], pdf: PdfText, name: str) -> tuple[SourceRow | None, str]:
    """By an id in the file name, the title field or the first two pages; else by title. An id naming two
    recorded sources is settled by title among them."""
    head = "\n".join((_ID_IN_NAME.sub(r"\1/", Path(name).stem), pdf.title or "", *pdf.pages[:2]))
    hits: dict[int, tuple[SourceRow, str]] = {}
    for key in ids_in(head):
        s = lit.source_by_key(key)
        if s is not None and s.id not in hits:
            hits[s.id] = (s, key)
    if len(hits) == 1:
        s, key = next(iter(hits.values()))
        return s, f"id {key}"
    pool = [s for s, _ in hits.values()] or rows
    scored = sorted(((max(title_score(c, t) for c in title_candidates(pdf, name) for t in _titles(s)), s.id, s)
                     for s in pool), key=lambda x: -x[0])
    if not scored or scored[0][0] < TITLE_FLOOR:
        return None, (f"no recorded source matches (best title score {scored[0][0]:.2f}); record it, or rename the "
                      "file to its DOI or title" if scored else "no recorded sources")
    if len(scored) > 1 and scored[0][0] - scored[1][0] < TITLE_MARGIN:
        return None, f"two sources match its title equally (#{scored[0][1]}, #{scored[1][1]}); rename it to its DOI"
    return scored[0][2], f"title {scored[0][0]:.2f}"


def _titles(s: SourceRow) -> list[str]:
    out = [t for t in (s.title, s.also_titled) if t]
    for t in list(out):
        main = t.split(":")[0]
        if main != t and len(normal_title(main).split()) >= MAIN_TITLE_WORDS:
            out.append(main)
    return out
