"""The literature index (research-lab 03): every method the lab knows of, the sources it came from and
the searches run for more, in the research database. Lookup by exact id (DOI, SSRN, arXiv, NBER,
cleaned URL) or fuzzy title gives the 5 nearest methods with score and funnel status; meaning search
is research-lab 04. The first open seeds the research archive, its 14 tested methods and ai-track
04-09. Marks (go / drop / dig deeper) keep who and when."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

from oracle.lab import load_seed
from oracle.lab.matching import link_for, normal_title, source_key, title_score
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, UnknownMethod
from oracle.services.testbed_service import research_context
from oracle.store.db import to_utc_iso, transaction
from oracle.store.literature import LiteratureRepo, MarkRow, MethodRow, SourceRow
from oracle.store.meta import MetaRepo

__all__ = ["MARKS", "QUALITIES", "STATUSES", "Lookup", "Method", "MethodDetail", "MethodHit", "Recorded", "Search",
           "Source", "SourceHit", "log_search", "lookup", "mark", "method", "methods", "record", "searches", "sources"]

STATUSES = ("new", "proposed", "go", "dropped", "triage", "power", "built", "train", "confirm", "pass", "fail",
            "promising", "not-testable", "duplicate-of")
QUALITIES = ("replicated", "peer-reviewed", "working-paper", "practitioner", "abstract-only")
MARKS = {"go": "go", "drop": "dropped", "dig": "triage"}
"""Dig deeper sends a method back to the literature stage (funnel 1)."""
MARKABLE = frozenset({"new", "proposed", "go", "dropped", "triage", "power", "not-testable"})
TESTING = frozenset({"built", "train", "confirm"})
NEAREST = 5
SOURCE_FLOOR = 0.6
"""Fuzzy source matches below this are not listed."""
SEED_KEY = "lab_seed"
SEED_BY = "seed"
NEW_PREFIX = "M-"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Source:
    id: int
    title: str
    authors: str
    year: int | None
    quality: str
    url: str | None
    read_on: str | None
    note: str
    added_at: str
    added_by: str
    keys: tuple[str, ...]
    methods: tuple[str, ...]
    """Refs of the methods it yielded."""


@dataclass(frozen=True)
class SourceHit:
    source: Source
    score: float


@dataclass(frozen=True)
class MethodHit:
    ref: str
    name: str
    family: str
    status: str
    rating: int | None
    score: float
    matched: str
    """What scored: "ref", "source id", "name", "alias: ..." or "source: <title>"."""


@dataclass(frozen=True)
class Lookup:
    query: str
    key: str | None
    """The id the query was read as; None for a title or name."""
    sources: list[SourceHit]
    methods: list[MethodHit]
    note: str


@dataclass(frozen=True)
class Method:
    ref: str
    name: str
    family: str
    definition: str
    aliases: tuple[str, ...]
    status: str
    duplicate_of: str | None
    origin: str
    """archive, tested, ai-track or added."""
    rating: int | None
    archive_status: str
    archive_note: str
    """The archive's status line verbatim, with its prior-test context."""
    evidence: str
    why: str
    result: str
    see_also: tuple[str, ...]
    test_key: str | None
    """research-lab 01's registry test name."""
    status_by: str
    status_at: str
    added_at: str


@dataclass(frozen=True)
class MethodDetail(Method):
    sources: tuple[Source, ...] = ()
    marks: tuple[MarkRow, ...] = ()


@dataclass(frozen=True)
class Recorded:
    source: Source | None
    created: bool
    """A new source (False: an existing one updated, or none given)."""
    method: Method | None
    nearest: list[MethodHit]


@dataclass(frozen=True)
class Search:
    id: int
    query: str
    place: str
    at: str
    by: str
    note: str
    found: tuple[Source, ...]


@contextmanager
def _lab(ctx: AppContext) -> Iterator[LiteratureRepo]:
    with research_context(ctx) as r:
        if MetaRepo(r.conn).get(SEED_KEY) is None:
            _seed(r.conn, to_utc_iso(ctx.clock.utcnow()))
        yield LiteratureRepo(r.conn)


def _seed(conn, at: str) -> None:
    seed = load_seed()
    repo = LiteratureRepo(conn)
    with transaction(conn):
        # Checked again under the write lock: another first open may have seeded since.
        if MetaRepo(conn).get(SEED_KEY) is not None:
            return
        by_url: dict[str, int] = {}
        for s in seed["sources"]:
            key = source_key(s["url"])
            known = repo.source_by_key(key)
            by_url[s["url"]] = known.id if known else repo.add_source(
                s["title"], s["authors"], s["year"], s["quality"], s["url"], None,
                "seeded from the research archive; quality inferred from the link", at, SEED_BY, [key])
        for m in seed["methods"]:
            new = repo.add_method(m["ref"], m["name"], m["status"], m["origin"], at, SEED_BY, family=m["family"],
                                  definition=m["definition"], rating=m["rating"], archive_status=m["archive_status"],
                                  archive_note=m["archive_note"], evidence=m["evidence"], why=m["why"],
                                  result=m["result"], see_also=m["see_also"], test_key=m["test_key"])
            repo.link(new, [by_url[u] for u in m["sources"]])
        MetaRepo(conn).set(SEED_KEY, str(seed["version"]))


def _source(repo: LiteratureRepo, s: SourceRow) -> Source:
    return Source(s.id, s.title, s.authors, s.year, s.quality, s.url, s.read_on, s.note, s.added_at, s.added_by,
                  tuple(repo.keys(s.id)), tuple(m.ref for m in repo.source_methods(s.id)))


def _method(m: MethodRow, refs: dict[int, str]) -> Method:
    return Method(m.ref, m.name, m.family, m.definition, m.aliases, m.status,
                  refs.get(m.duplicate_of) if m.duplicate_of else None, m.origin, m.rating, m.archive_status,
                  m.archive_note, m.evidence, m.why, m.result, m.see_also, m.test_key, m.status_by, m.status_at,
                  m.added_at)


def _detail(repo: LiteratureRepo, m: MethodRow) -> MethodDetail:
    refs = {x.id: x.ref for x in repo.methods()} if m.duplicate_of else {}
    return MethodDetail(**vars(_method(m, refs)), sources=tuple(_source(repo, s) for s in repo.method_sources(m.id)),
                        marks=tuple(repo.marks(m.id)))


def _hit(m: MethodRow, score: float, matched: str) -> MethodHit:
    return MethodHit(m.ref, m.name, m.family, m.status, m.rating, round(score, 3), matched)


def _known(repo: LiteratureRepo, ref: str) -> MethodRow:
    m = repo.method(ref.strip())
    if m is None:
        raise UnknownMethod(f"no method {ref.strip()!r} in the literature index")
    return m


def _nearest(repo: LiteratureRepo, queries: Sequence[str], skip: frozenset[int] = frozenset(),
             limit: int = NEAREST) -> tuple[list[MethodHit], list[SourceHit]]:
    """Each method's best score over its ref, name, aliases and its sources' titles, for any query."""
    all_sources = repo.sources()
    source_score = {s.id: max(title_score(q, text) for q in queries for text in _source_texts(s))
                    for s in all_sources}
    by_method: dict[int, list[int]] = {}
    for mid, sid in repo.links():
        by_method.setdefault(mid, []).append(sid)
    titles = {s.id: s.title for s in all_sources}
    hits = []
    for m in repo.methods():
        if m.id in skip:
            continue
        best = (0.0, "")
        for q in queries:
            if q.strip().lower() == m.ref.lower():
                best = max(best, (1.0, "ref"))
            best = max(best, (title_score(q, m.name), "name"),
                       *((title_score(q, a), f"alias: {a}") for a in m.aliases))
        for sid in by_method.get(m.id, ()):
            best = max(best, (source_score[sid], f"source: {titles[sid]}"))
        hits.append(_hit(m, *best))
    hits.sort(key=lambda h: -h.score)
    found = sorted((s for s in all_sources if source_score[s.id] >= SOURCE_FLOOR), key=lambda s: -source_score[s.id])
    return hits[:limit], [SourceHit(_source(repo, s), round(source_score[s.id], 3)) for s in found[:limit]]


def _source_texts(s: SourceRow) -> tuple[str, ...]:
    """What a query may name a source by: its title, authors and title, or authors and year."""
    if not s.authors:
        return (s.title,)
    return s.title, f"{s.authors} {s.title}", f"{s.authors} {s.year or ''}"


def lookup(ctx: AppContext, query: str) -> Lookup:
    """An id finds its source exactly; anything else is matched fuzzily on titles, names and refs."""
    q = query.strip()
    if not q:
        raise InvalidQuery("type a title, a method name or ref, or an id (DOI, SSRN, arXiv, NBER, URL)")
    key = source_key(q)
    with _lab(ctx) as repo:
        if key is None:
            hits, found = _nearest(repo, [q])
            return Lookup(q, None, found, hits, "fuzzy match on titles, names and refs")
        s = repo.source_by_key(key)
        if s is None:
            return Lookup(q, key, [], [], f"{key} is not in the index yet: record it")
        yielded = [_hit(m, 1.0, "source id") for m in repo.source_methods(s.id)]
        return Lookup(q, key, [SourceHit(_source(repo, s), 1.0)], yielded[:NEAREST], "exact id match")


def sources(ctx: AppContext) -> list[Source]:
    with _lab(ctx) as repo:
        return [_source(repo, s) for s in repo.sources()]


def methods(ctx: AppContext, status: str | None = None) -> list[Method]:
    if status is not None and status not in STATUSES:
        raise InvalidQuery(f"status is one of {', '.join(STATUSES)}, got {status!r}")
    with _lab(ctx) as repo:
        rows = repo.methods(status)
        refs = {m.id: m.ref for m in (rows if status is None else repo.methods())}
        return [_method(m, refs) for m in rows]


def method(ctx: AppContext, ref: str) -> MethodDetail:
    with _lab(ctx) as repo:
        return _detail(repo, _known(repo, ref))


def _by(by: str) -> str:
    by = by.strip()
    if not by or len(by) > 40:
        raise InvalidQuery("by names who did it (1 to 40 characters), e.g. user or autopilot")
    return by


def mark(ctx: AppContext, ref: str, how: str, by: str = "user", note: str = "") -> MethodDetail:
    """go, drop or dig (deeper): the method's new status, with who and when, kept in its mark history."""
    if how not in MARKS:
        raise InvalidQuery(f"a mark is go, drop or dig, got {how!r}")
    by = _by(by)
    with _lab(ctx) as repo:
        m = _known(repo, ref)
        if m.status in ("pass", "fail", "promising"):
            raise InvalidQuery(f"{m.ref} has a verdict ({m.status}): a FAIL is never re-tested with tweaks; "
                               "record a new method that says how it differs")
        if m.status in TESTING:
            raise InvalidQuery(f"{m.ref} is being tested ({m.status}): its test moves it on, not a mark")
        if m.status == "duplicate-of" and how != "drop":
            raise InvalidQuery(f"{m.ref} is a near-duplicate of a tested method: only drop marks it; "
                               "registration asks for a 'different because' note")
        repo.set_status(m.id, how, MARKS[how], by, to_utc_iso(ctx.clock.utcnow()), note.strip())
        return _detail(repo, _known(repo, ref))


def record(ctx: AppContext, ident: str | None = None, *, title: str | None = None, authors: str | None = None,
           year: int | None = None, quality: str | None = None, note: str | None = None, read_on: str | None = None,
           methods: Iterable[str] = (), new_method: str | None = None, family: str = "", definition: str = "",
           aliases: Iterable[str] = (), by: str = "user") -> Recorded:
    """A source (new, or an existing one found by any of its ids or its exact title, updated with what is
    given), optionally linked to methods it yields, and optionally a new method; the 5 nearest methods
    come back so a near-duplicate shows before more work goes into it."""
    by = _by(by)
    title = (title or "").strip() or None
    new_method = (new_method or "").strip() or None
    aliases = [a.strip() for a in aliases if a.strip()]
    refs = [r.strip() for r in methods if r.strip()]
    key = None
    if ident is not None and ident.strip():
        key = source_key(ident)
        if key is None:
            raise InvalidQuery(f"not a DOI, SSRN, arXiv or NBER id or a URL: {ident!r} (a title goes in --title)")
    if key is None and title is None and new_method is None:
        raise InvalidQuery("nothing to record: give a source id or URL, a title, or a new method")
    if quality is not None and quality not in QUALITIES:
        raise InvalidQuery(f"quality is one of {', '.join(QUALITIES)}, got {quality!r}")
    if year is not None and not 1800 <= year <= ctx.clock.utcnow().year + 1:
        raise InvalidQuery(f"year {year} is not a publication year")
    if read_on is not None and (not _DATE.match(read_on) or not _is_date(read_on)):
        raise InvalidQuery(f"read date is YYYY-MM-DD, got {read_on!r}")
    at = to_utc_iso(ctx.clock.utcnow())
    with _lab(ctx) as repo:
        linked = [_known_for_record(repo, r) for r in refs]
        existing = repo.source_by_key(key) if key else None
        if existing is None and title:
            existing = next((s for s in repo.sources() if normal_title(s.title) == normal_title(title)), None)
        has_source = key is not None or title is not None
        if existing is None and key is not None and title is None:
            raise InvalidQuery(f"{key} is new: a new source needs a title")
        if linked and not has_source:
            raise InvalidQuery("linking methods needs a source: give its id, URL or title")
        if new_method is not None:
            same = next((m for m in repo.methods() if normal_title(m.name) == normal_title(new_method)), None)
            if same is not None:
                raise InvalidQuery(f"{new_method!r} is already in the index as {same.ref}")
        with repo.atomic():
            source_id = None
            if existing is not None:
                fields = {k: v for k, v in (("title", title), ("authors", authors), ("year", year), ("quality", quality),
                                            ("note", note), ("read_on", read_on)) if v is not None}
                if existing.url is None and key is not None:
                    fields["url"] = _link(ident, key)
                repo.update_source(existing.id, **fields)
                repo.add_keys(existing.id, [key] if key else [])
                source_id = existing.id
            elif has_source:
                source_id = repo.add_source(title, authors or "", year, quality or "abstract-only",
                                            _link(ident, key) if key else None, read_on, note or "", at, by,
                                            [key] if key else [])
            made = None
            if new_method is not None:
                made = repo.add_method(_next_ref(repo), new_method, "new", "added", at, by, family=family.strip(),
                                       definition=definition.strip(), aliases=aliases)
                linked.append(repo.method_by_id(made))
            if source_id is not None:
                for m in linked:
                    repo.link(m.id, [source_id])
        queries = [q for q in (new_method, *aliases, title) if q]
        hits, _ = _nearest(repo, queries, frozenset({made}) if made else frozenset()) if queries else ([], [])
        new_row = repo.method_by_id(made) if made else None
        return Recorded(_source(repo, repo.source(source_id)) if source_id is not None else None,
                        existing is None and source_id is not None,
                        _method(new_row, {}) if new_row else None, hits)


def _known_for_record(repo: LiteratureRepo, ref: str) -> MethodRow:
    m = repo.method(ref)
    if m is None:
        raise InvalidQuery(f"no method {ref!r} in the literature index")
    return m


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _link(ident: str | None, key: str) -> str:
    """The link as given when it is one; else one built from the id."""
    raw = (ident or "").strip()
    return raw if raw.lower().startswith(("http://", "https://")) else link_for(key)


def _next_ref(repo: LiteratureRepo) -> str:
    taken = [int(r[len(NEW_PREFIX):]) for r in repo.refs(NEW_PREFIX) if r[len(NEW_PREFIX):].isdigit()]
    return f"{NEW_PREFIX}{max(taken, default=0) + 1:03d}"


def log_search(ctx: AppContext, query: str, where: str, found: Iterable[str] = (), note: str = "",
               by: str = "user") -> Search:
    """A search run (what, where, when, who) and the recorded sources it turned up."""
    query, where, by = query.strip(), where.strip(), _by(by)
    if not query:
        raise InvalidQuery("a search needs the query that was run")
    if not where:
        raise InvalidQuery("say where the search ran (where), e.g. SSRN, Google Scholar, OSAP")
    with _lab(ctx) as repo:
        ids = []
        for f in (x.strip() for x in found if x.strip()):
            key = source_key(f)
            s = repo.source_by_key(key) if key else None
            if s is None:
                raise InvalidQuery(f"{f!r} is not a recorded source: record it first")
            ids.append(s.id)
        new = repo.add_search(query, where, to_utc_iso(ctx.clock.utcnow()), by, note.strip(), dict.fromkeys(ids))
        return next(s for s in _searches(repo, 50) if s.id == new)


def searches(ctx: AppContext, limit: int = 20) -> list[Search]:
    """Newest first."""
    with _lab(ctx) as repo:
        return _searches(repo, limit)


def _searches(repo: LiteratureRepo, limit: int) -> list[Search]:
    return [Search(s.id, s.query, s.place, s.at, s.by, s.note,
                   tuple(_source(repo, repo.source(i)) for i in s.source_ids)) for s in repo.searches(limit)]
