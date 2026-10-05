"""The results table (research-lab 06): every index method and every registered test once, the stage
each reached, its verdict, deaths by stage, filters, and the JSON the weekly page (08) reads."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from oracle.config import Config
from oracle.services import literature_service as lab
from oracle.services import results_service as results
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.lab_service import scoreboard
from oracle.services.literature_service import open_index
from oracle.store.literature import LiteratureRepo
from tests.fakes.clock import FakeClock
from tests.fakes.results import build

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
SNAPSHOT = Path(__file__).parent / "snapshots" / "results_table.json"


@pytest.fixture
def ctx(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW)) as c:
        yield c


@pytest.fixture
def built(ctx, monkeypatch):
    build(ctx, monkeypatch)
    return ctx


def test_every_seeded_method_and_every_registered_test_appears_once(ctx):
    table = results.results_table(ctx)
    refs = Counter(r.ref for r in table.rows if r.ref)
    assert set(refs) == {m.ref for m in lab.methods(ctx)} and set(refs.values()) == {1}
    keys = Counter(t.key for r in table.rows for t in r.tests)
    assert set(keys) == {r.key for r in scoreboard(ctx).rows} and set(keys.values()) == {1}
    x07 = next(r for r in table.rows if r.ref == "X-07")
    assert [t.key for t in x07.tests] == ["event:2.02", "event:2.01", "event:insider_cluster"]


def test_each_row_shows_the_stage_it_reached_and_its_verdict(built):
    rows = {r.ref or r.tests[0].key: r for r in results.results_table(built).rows}
    got = {k: (r.stage, r.verdict, r.died_at) for k, r in rows.items()}
    assert got == {
        "A-01": ("candidate", "open", None),
        "T-13": ("literature", "fail", "literature"),
        "T-07": ("power", "could-not-see", "power"),
        "T-08": ("power", "open", None),
        "X-01": ("confirm", "fail", "confirm"),
        "X-11": ("unrecorded", "fail", "unrecorded"),
        "A-02": ("candidate", "dropped", None),
        "M-001": ("build", "open", None),
        "event:1.01": ("confirm", "pass", None),
        "trend_overlay": ("confirm", "fail", "confirm"),
    }
    a = rows["A-01"]
    assert a.quality == "replicated" and [s.quality for s in a.sources] == ["replicated", "practitioner"]
    assert a.evidence == "GPT scores predict next-day returns." and [(n.mark, n.note) for n in a.notes] == [
        ("go", "worth a look")]
    assert rows["T-13"].osap == "Mom12m" and rows["T-13"].triage.outcome == "stop" and rows["T-13"].power is None
    mom = rows["X-01"].tests[0]
    assert (mom.key, mom.verdict) == ("momentum_12_1", "fail")
    line = mom.lines[0]
    assert (line.train, line.confirm, line.t, line.turnover, line.break_even) == (0.0123, 0.0071, 0.45, 0.31, 0.0042)
    assert rows["M-001"].missing_tests == ("gone_key",)


def test_deaths_are_counted_by_the_stage_they_stopped_at(built):
    table = results.results_table(built)
    assert table.deaths == {"literature": 1, "power": 1, "build": 0, "train": 0, "confirm": 2, "unrecorded": 1}
    assert table.verdicts == {"pass": 1, "promising": 0, "fail": 4, "could-not-see": 1, "not-testable": 0,
                              "inconclusive": 0, "dropped": 1, "duplicate": 0, "open": 3}


@pytest.mark.parametrize("filters, shown", [
    ({"family": "1. LLM and NLP on text"}, ["A-01", "A-02"]),
    ({"stage": "power"}, ["T-07", "T-08"]),
    ({"stage": "funnel"}, ["T-13", "T-07", "T-08", "X-01", "X-11", "M-001", "event:1.01", "trend_overlay"]),
    ({"verdict": "fail"}, ["T-13", "X-01", "X-11", "trend_overlay"]),
    ({"stage": "confirm", "verdict": "fail"}, ["X-01", "trend_overlay"]),
])
def test_filters_by_family_stage_and_verdict_and_count_what_they_show(built, filters, shown):
    table = results.results_table(built, **filters)
    assert [r.ref or r.tests[0].key for r in table.rows] == shown
    assert sum(table.verdicts.values()) == len(shown)
    assert table.families == ("1. LLM and NLP on text", "A. Cross-sectional stock anomalies",
                              "Already tested (signal)", "Already tested (strategy)")


@pytest.mark.parametrize("filters, why", [({"stage": "nowhere"}, "stage is one of"),
                                          ({"verdict": "maybe"}, "verdict is one of")])
def test_an_unknown_filter_is_refused(built, filters, why):
    with pytest.raises(InvalidQuery, match=why):
        results.results_table(built, **filters)


def test_a_test_two_methods_name_shows_once_under_the_first(built):
    with open_index(built) as r:
        LiteratureRepo(r.conn).add_method("M-002", "Momentum again", "new", "added", "2026-10-04", "user",
                                          test_keys=("momentum_12_1",))
    rows = {r.ref: r for r in results.results_table(built).rows if r.ref}
    assert [t.key for t in rows["X-01"].tests][0] == "momentum_12_1"
    assert rows["M-002"].tests == () and rows["M-002"].shared_tests == ("momentum_12_1",)


def test_the_json_is_stable(built):
    """The weekly page (research-lab 08) reads this; a change here is a change to its contract. To
    accept a deliberate change, delete the snapshot and run this test once."""
    got = json.loads(json.dumps(results.table_json(results.results_table(built))))
    if not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.fail("snapshot written: check it by eye, then run again")
    assert got == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert got["version"] == results.JSON_VERSION


def test_a_could_not_see_sent_back_to_the_literature_is_open_again(built):
    lab.mark(built, "T-07", "dig", tests=False)
    row = next(r for r in results.results_table(built).rows if r.ref == "T-07")
    assert (row.status, row.verdict, row.died_at) == ("triage", "open", None)
