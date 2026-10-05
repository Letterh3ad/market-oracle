"""The research lab's results table (research-lab 06, spec 15 and 17): every method in the literature
index and every registered test, once each, with its sources and their quality tags, the literature's
evidence, the funnel stage it reached, its triage and power runs, its tests' train and confirm figures
against the bar, turnover and break-even cost, its verdict and its notes; deaths counted by the stage
they stopped at. `table_json` is the stable form the weekly page (research-lab 08) reads."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.lab_service import ScoreRow, scoreboard
from oracle.services.literature_service import QUALITIES, open_index
from oracle.services.triage_service import StageEntry
from oracle.store.literature import LiteratureRepo, MethodRow
from oracle.store.triage import StageRow, TriageRepo

__all__ = ["DEATHS", "JSON_VERSION", "STAGES", "VERDICTS", "Note", "ResultRow", "ResultsTable", "SourceTag",
           "TestLine", "TestResult", "TestedUnder", "results_table", "table_json"]

JSON_VERSION = 1
FUNNEL = ("literature", "power", "build", "train", "confirm")
"""Spec 5's stages, cheapest first."""
UNRECORDED = "unrecorded"
"""A verdict from before the funnel recorded where it stopped (the archive's own tests)."""
STAGES = ("candidate", *FUNNEL, UNRECORDED)
FUNNEL_FILTER = "funnel"
"""Every stage but candidate."""
VERDICTS = ("pass", "promising", "fail", "could-not-see", "not-testable", "inconclusive", "dropped", "duplicate",
            "open")
DEATHS = frozenset({"fail", "could-not-see", "not-testable"})
_STATUS_STAGE = {"triage": "literature", "power": "power", "built": "build", "train": "train", "confirm": "confirm"}
_STATUS_VERDICT = {"pass": "pass", "promising": "promising", "dropped": "dropped", "duplicate-of": "duplicate",
                   "not-testable": "not-testable", "fail": "fail"}


@dataclass(frozen=True)
class SourceTag:
    id: int
    title: str
    authors: str
    year: int | None
    quality: str
    url: str | None


@dataclass(frozen=True)
class Note:
    """A go / drop / dig mark on the method, with who, when and what they wrote."""
    mark: str
    by: str
    at: str
    note: str


@dataclass(frozen=True)
class TestLine:
    """One horizon of a registered test (a strategy has one line, no horizon)."""
    horizon: int | None
    measure: str
    train: float | None
    confirm: float | None
    t: float | None
    power: float | None
    turnover: float | None
    break_even: float | None
    dsr: float | None
    verdict: str
    """As the harness judged it, word for word."""
    run_at: str | None


@dataclass(frozen=True)
class TestResult:
    key: str
    """The registration's key."""
    label: str
    kind: str
    registered_on: str
    bar: str
    legacy: bool
    commit_hash: str | None
    note: str
    verdict: str
    """Over its lines, in VERDICTS' words."""
    lines: tuple[TestLine, ...]


@dataclass(frozen=True)
class TestedUnder:
    """A test of this method an earlier method shows, with where it got to there."""
    key: str
    ref: str
    stage: str
    verdict: str


@dataclass(frozen=True)
class ResultRow:
    ref: str | None
    """The index method, whose notes are its marks; None for a test no method names."""
    name: str
    family: str
    status: str
    """The index's funnel status; empty for a test no method names."""
    origin: str
    rating: int | None
    quality: str | None
    """The best quality tag among its sources."""
    sources: tuple[SourceTag, ...]
    evidence: str
    result: str
    osap: str | None
    triage: StageEntry | None
    """The newest triage run."""
    power: StageEntry | None
    tests: tuple[TestResult, ...]
    shared_tests: tuple[str, ...]
    """Its tests an earlier method in the index already shows."""
    missing_tests: tuple[str, ...]
    """Its test keys the registry does not hold (dropped since)."""
    stage: str
    verdict: str
    died_at: str | None
    """The stage it stopped at, when the verdict is a death."""
    notes: tuple[Note, ...]
    """Newest first."""
    tested_under: tuple[TestedUnder, ...] = ()
    """shared_tests with the method each shows under, so a reader does not take this one as untested."""


@dataclass(frozen=True)
class ResultsTable:
    as_of: str
    filters: dict[str, str | None]
    rows: tuple[ResultRow, ...]
    deaths: dict[str, int]
    """Rows shown whose verdict is a death, by the stage they stopped at."""
    verdicts: dict[str, int]
    """Rows shown, by verdict."""
    families: tuple[str, ...]
    """Every family in the index, for the filter."""
    stages: tuple[str, ...] = STAGES
    verdict_words: tuple[str, ...] = VERDICTS


def _word(verdict: str) -> str:
    """A harness's verdict text in VERDICTS' words."""
    if verdict.startswith("PASS"):
        return "pass"
    if verdict.startswith("FAIL"):
        return "fail"
    return "inconclusive" if verdict.startswith("INCONCLUSIVE") else "open"


def _combined(words: list[str]) -> str:
    """Any PASS wins; one still open keeps it open; otherwise an inconclusive one, then fail."""
    if not words:
        return "open"
    for w in ("pass", "open", "inconclusive"):
        if w in words:
            return w
    return "fail"


def _test(key: str, rows: list[ScoreRow]) -> TestResult:
    first = rows[0]
    lines = tuple(TestLine(r.horizon, r.measure, r.train, r.value, r.t, r.power, r.turnover, r.break_even, r.dsr,
                           r.verdict, r.run_at) for r in rows)
    return TestResult(key, first.label, first.kind, first.registered_on, first.bar, first.legacy, first.commit_hash,
                      first.note, _combined([_word(x.verdict) for x in lines]), lines)


def _test_stage(tests: tuple[TestResult, ...]) -> str:
    lines = [x for t in tests for x in t.lines]
    return ("confirm" if any(x.confirm is not None for x in lines)
            else "train" if any(x.train is not None for x in lines) else "build")


def _stage(status: str, stages: dict[str, StageRow], tests: tuple[TestResult, ...]) -> str:
    reached = ["candidate", _STATUS_STAGE.get(status, "candidate")]
    if "triage" in stages:
        reached.append("literature")
    if "power" in stages:
        reached.append("power")
    if tests:
        reached.append(_test_stage(tests))
    best = max(reached, key=STAGES.index)
    if best == "candidate" and status in ("fail", "not-testable", "pass", "promising"):
        return UNRECORDED
    return best


def _verdict(status: str, stages: dict[str, StageRow], tests: tuple[TestResult, ...]) -> str:
    if status in ("pass", "promising", "dropped", "duplicate-of"):
        return _STATUS_VERDICT[status]
    if status == "not-testable" and "power" in stages and stages["power"].outcome == "stop":
        return "could-not-see"
    if status in _STATUS_VERDICT:
        return _STATUS_VERDICT[status]
    return _combined([t.verdict for t in tests]) if tests else "open"


def _entry(s: StageRow | None) -> StageEntry | None:
    return None if s is None else StageEntry(s.stage, s.outcome, s.summary, s.detail, s.status_from, s.status_to,
                                             s.by, s.at)


def _row(ref: str | None, name: str, family: str, status: str, origin: str, rating: int | None,
         sources: tuple[SourceTag, ...], evidence: str, result: str, osap: str | None, stages: dict[str, StageRow],
         tests: tuple[TestResult, ...], shared: tuple[str, ...], missing: tuple[str, ...],
         notes: tuple[Note, ...], tested_under: tuple[TestedUnder, ...] = ()) -> ResultRow:
    verdict = _verdict(status, stages, tests)
    stage = _stage(status, stages, tests)
    quality = min((s.quality for s in sources), key=QUALITIES.index, default=None)
    return ResultRow(ref, name, family, status, origin, rating, quality, sources, evidence, result, osap,
                     _entry(stages.get("triage")), _entry(stages.get("power")), tests, shared, missing, stage, verdict,
                     stage if verdict in DEATHS else None, notes, tested_under)


def _method_row(repo: LiteratureRepo, m: MethodRow, osap: str | None, stages: dict[str, StageRow],
                tests: dict[str, TestResult], shown: dict[str, str]) -> ResultRow:
    mine = tuple(tests[k] for k in m.test_keys if k in tests and k not in shown)
    shared = tuple(k for k in m.test_keys if k in tests and k in shown)
    under = tuple(TestedUnder(k, shown[k], _test_stage((tests[k],)), tests[k].verdict) for k in shared)
    shown.update((t.key, m.ref) for t in mine)
    sources = tuple(SourceTag(s.id, s.title, s.authors, s.year, s.quality, s.url) for s in repo.method_sources(m.id))
    notes = tuple(Note(k.mark, k.by, k.at, k.note) for k in repo.marks(m.id))
    return _row(m.ref, m.name, m.family, m.status, m.origin, m.rating, sources, m.evidence, m.result, osap, stages,
                mine, shared, tuple(k for k in m.test_keys if k not in tests), notes, under)


def _check(name: str, value: str | None, allowed: tuple[str, ...]) -> None:
    if value is not None and value not in allowed:
        raise InvalidQuery(f"{name} is one of {', '.join(allowed)}, got {value!r}")


def _shown(r: ResultRow, family: str | None, stage: str | None, verdict: str | None) -> bool:
    return ((family is None or r.family == family)
            and (stage is None or r.stage == stage or (stage == FUNNEL_FILTER and r.stage != "candidate"))
            and (verdict is None or r.verdict == verdict))


def results_table(ctx: AppContext, family: str | None = None, stage: str | None = None,
                  verdict: str | None = None) -> ResultsTable:
    """Methods in the index's order, then the tests no method names in registration order. A test
    two methods name shows under the first. Filters narrow the rows and the counts with them."""
    family, stage, verdict = (x.strip() or None if x is not None else None for x in (family, stage, verdict))
    _check("stage", stage, (*STAGES, FUNNEL_FILTER))
    _check("verdict", verdict, VERDICTS)
    by_key: dict[str, list[ScoreRow]] = {}
    for s in scoreboard(ctx).rows:
        by_key.setdefault(s.key, []).append(s)
    tests = {k: _test(k, rows) for k, rows in by_key.items()}
    with open_index(ctx) as r:
        lit, tri = LiteratureRepo(r.conn), TriageRepo(r.conn)
        mapped = tri.mapped()
        newest: dict[int, dict[str, StageRow]] = {}
        for s in tri.stages():
            newest.setdefault(s.method_id, {})[s.stage] = s
        shown: dict[str, str] = {}
        methods = lit.methods()
        rows = [_method_row(lit, m, mapped.get(m.id), newest.get(m.id, {}), tests, shown) for m in methods]
    rows += [_row(None, t.label, "", "", "", None, (), "", "", None, {}, (t,), (), (), ())
             for k, t in tests.items() if k not in shown]
    kept = tuple(x for x in rows if _shown(x, family, stage, verdict))
    died = Counter(x.died_at for x in kept if x.died_at)
    said = Counter(x.verdict for x in kept)
    return ResultsTable(ctx.clock.utcnow().isoformat(), {"family": family, "stage": stage, "verdict": verdict}, kept,
                        {s: died[s] for s in (*FUNNEL, UNRECORDED)}, {v: said[v] for v in VERDICTS},
                        tuple(sorted({m.family for m in methods if m.family})))


def table_json(t: ResultsTable) -> dict:
    """The table as plain data, its keys in a fixed order, with a version the reader checks."""
    return {"version": JSON_VERSION, **asdict(t)}
