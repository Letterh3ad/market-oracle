"""The research lab's weekly page (research-lab 08, spec 15): build it from the results table, the papers I'd
like and the open questions, write it where the autopilot publishes it from, and apply the marks its readers
left in the page's database back to the literature index through the mark service, with who and when."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from oracle.lab.weekly_page import CAPABILITIES, COLLECTION, Candidate, Paper, pick_candidates, render_page
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, UnknownMethod
from oracle.services.literature_service import MARKS, method, mark, open_index
from oracle.services.papers_service import wanted
from oracle.services.results_service import results_table, table_json
from oracle.store.db import to_utc_iso
from oracle.store.meta import MetaRepo

__all__ = ["CAPABILITIES", "COLLECTION", "PAGE_PATH", "MarkApplied", "PageMade", "apply_marks", "default_path",
           "last_page", "make_page"]

PAGE_PATH = Path(".scratch/research-lab/weekly/page.html")
"""Beside oracle.toml, like the inbox: the same place whichever session builds it."""
META_KEY = "lab_weekly_page"
PAGE_BY = "page"
"""Who a mark is logged as when the page gave no usable viewer id."""
BY_CHARS = 40
OUTCOMES = ("applied", "already", "superseded", "no-mark", "unknown", "refused")


@dataclass(frozen=True)
class PageMade:
    path: str
    at: str
    candidates: tuple[str, ...]
    rows: int
    papers: int
    questions: int


@dataclass(frozen=True)
class MarkApplied:
    id: str
    outcome: str
    """One of OUTCOMES: applied, or why not."""
    detail: str


def default_path(ctx: AppContext) -> Path:
    return (ctx.config_source.parent if ctx.config_source else Path.cwd()) / PAGE_PATH


def _paper(w) -> Paper:
    s = w.source
    return Paper(s.title, s.authors, s.year, s.url, w.reason, w.note)


def make_page(ctx: AppContext, questions: list[str] | tuple[str, ...] = (), out: Path | None = None) -> PageMade:
    """Writes the page and remembers where and when, for the Research tab."""
    table = table_json(results_table(ctx))
    papers = [_paper(w) for w in wanted(ctx)]
    candidates: list[Candidate] = pick_candidates(table)
    qs = [q.strip() for q in questions if q.strip()]
    at = to_utc_iso(ctx.clock.utcnow())
    path = Path(out) if out is not None else default_path(ctx)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_page(table, papers, candidates, qs, at), encoding="utf-8")
    except OSError as exc:
        raise InvalidQuery(f"cannot write the weekly page to {path}: {exc}") from exc
    made = PageMade(str(path.resolve()), at, tuple(c.ref for c in candidates), len(table["rows"]), len(papers),
                    len(qs))
    with open_index(ctx) as r:
        MetaRepo(r.conn).set(META_KEY, json.dumps(asdict(made)))
    return made


def last_page(ctx: AppContext) -> PageMade | None:
    with open_index(ctx) as r:
        raw = MetaRepo(r.conn).get(META_KEY)
    if raw is None:
        return None
    d = json.loads(raw)
    return PageMade(**{**d, "candidates": tuple(d["candidates"])})


def _when(at: object) -> datetime | None:
    if not isinstance(at, str):
        return None
    try:
        t = datetime.fromisoformat(at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _who(by: object, note: str) -> tuple[str, str]:
    """A viewer id fits the mark log's who unless it is long; then the log says page and the note keeps it."""
    by = by.strip() if isinstance(by, str) else ""
    if not by:
        return PAGE_BY, note
    if len(by) > BY_CHARS:
        return PAGE_BY, f"{note} (page viewer {by})".strip()
    return by, note


def _one(ctx: AppContext, doc: dict, at: datetime) -> MarkApplied:
    key, how = doc["id"].strip(), doc.get("mark") or ""
    note = doc.get("note") if isinstance(doc.get("note"), str) else ""
    if not how:
        return MarkApplied(key, "no-mark", "a note with no mark: nothing to apply")
    if how not in MARKS:
        return MarkApplied(key, "refused", f"a mark is go, drop or dig, got {how!r}")
    try:
        m = method(ctx, key, tests=False)
    except UnknownMethod as exc:
        return MarkApplied(key, "unknown", str(exc))
    stamp = to_utc_iso(at)
    if any(_when(k.at) == at for k in m.marks):
        return MarkApplied(key, "already", f"the {how} mark made {stamp} is already in the index")
    if m.marks and _when((newest := max(m.marks, key=lambda k: _when(k.at))).at) > at:
        return MarkApplied(key, "superseded", f"a later mark stands: {newest.mark} by {newest.by} at {newest.at}")
    by, note = _who(doc.get("by"), note.strip())
    try:
        done = mark(ctx, key, how, by, note, tests=False, at=at)
    except InvalidQuery as exc:
        return MarkApplied(key, "refused", str(exc))
    return MarkApplied(key, "applied", f"{how}: now {done.status}, by {by} at {stamp}")


def apply_marks(ctx: AppContext, docs: object) -> list[MarkApplied]:
    """Each page mark, oldest first, through the mark service. Idempotent: a mark the index already holds at
    the same time is skipped, as is one an index mark made later overrides. Nothing is refused silently."""
    if not isinstance(docs, list):
        raise InvalidQuery("marks are a JSON list of {id, mark, note, by, at}, as the page's database holds them")
    out: list[MarkApplied] = []
    good: list[tuple[datetime, dict]] = []
    for i, d in enumerate(docs):
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"].strip():
            out.append(MarkApplied(f"#{i + 1}", "refused", "not a mark: needs an id (the method ref)"))
        elif (at := _when(d.get("at"))) is None:
            out.append(MarkApplied(d["id"], "refused", f"needs an ISO time with a zone in at, got {d.get('at')!r}"))
        else:
            good.append((at, d))
    good.sort(key=lambda x: x[0])
    return out + [_one(ctx, d, at) for at, d in good]
