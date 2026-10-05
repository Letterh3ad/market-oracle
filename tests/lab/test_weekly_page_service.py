"""The weekly page service (research-lab 08): the page built from the index and written beside oracle.toml, and
the page's marks applied back through the mark service, idempotent, with unknown methods reported."""

from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services import literature_service as lab
from oracle.services import weekly_page_service as weekly
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.literature_service import open_index
from oracle.store.literature import LiteratureRepo
from tests.fakes.clock import FakeClock
from tests.fakes.results import AT, build

NOW = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, tmp_path / "oracle.toml", FakeClock(NOW)) as c:
        build(c, monkeypatch)
        with open_index(c) as r:
            lit = LiteratureRepo(r.conn)
            for ref, rating in (("N-01", 3), ("N-02", 8), ("N-03", 1)):
                lit.add_method(ref, f"New idea {ref}", "new", "archive", AT, "seed", family="B. Timing", rating=rating)
        yield c


def test_the_page_lands_beside_oracle_toml_and_is_remembered(ctx, tmp_path):
    assert weekly.last_page(ctx) is None
    made = weekly.make_page(ctx, ["Widen to mid caps?", " "])
    path = tmp_path / ".scratch" / "research-lab" / "weekly" / "page.html"
    assert made.path == str(path.resolve()) and path.exists()
    assert made.candidates == ("N-02", "N-01", "N-03") and made.rows == 13 and made.questions == 1
    html = path.read_text(encoding="utf-8")
    assert 'data-key="N-02"' in html and "Widen to mid caps?" in html and "Generated 2026-10-05 08:00 UTC" in html
    assert weekly.last_page(ctx) == made


def test_the_page_goes_where_it_is_told(ctx, tmp_path):
    made = weekly.make_page(ctx, out=tmp_path / "elsewhere" / "w.html")
    assert made.path.endswith("w.html") and (tmp_path / "elsewhere" / "w.html").exists()


def _doc(ref, how, at="2026-10-05T07:30:00.000Z", note="", by="u_1", mark_at=None):
    return {"id": ref, "mark": how, "note": note, "by": by, "at": at, "mark_at": mark_at or at}


def test_marks_apply_with_who_and_when_and_never_twice(ctx):
    docs = [_doc("N-02", "go", note="strong"), _doc("N-03", "drop", by="u_" + "x" * 60), _doc("N-01", "dig")]
    first = weekly.apply_marks(ctx, docs)
    assert [(r.id, r.outcome) for r in first] == [("N-02", "applied"), ("N-03", "applied"), ("N-01", "applied")]
    n2 = lab.method(ctx, "N-02", tests=False)
    assert n2.status == "go" and (n2.marks[0].by, n2.marks[0].at, n2.marks[0].note) == (
        "page:u_1", "2026-10-05T07:30:00+00:00", "strong")
    n3 = lab.method(ctx, "N-03", tests=False).marks[0]
    assert n3.by == "page" and n3.note == f"(page viewer u_{'x' * 60})"
    assert lab.method(ctx, "N-01", tests=False).status == "triage"
    again = weekly.apply_marks(ctx, docs)
    assert {r.outcome for r in again} == {"already"}
    assert len(lab.method(ctx, "N-02", tests=False).marks) == 1


def test_unknown_odd_and_refused_marks_are_reported_not_fatal(ctx):
    got = weekly.apply_marks(ctx, [
        _doc("ZZ-9", "go"), _doc("N-02", ""), _doc("N-02", "maybe"), "junk", {"id": "N-01", "mark": "go", "at": "soon"},
        _doc("T-13", "go"), _doc("N-01", "go", by=None), {"id": ["N-02"], "mark": "go", "at": "2026-10-05T07:30Z"},
        _doc("N-02", ["go"]), _doc("N-02", {"go": 1}), _doc("N-03", "drop", mark_at="2026-10-05T07:31:00Z")])
    assert [(r.id, r.outcome) for r in got] == [
        ("#4", "refused"), ("N-01", "refused"), ("#8", "refused"), ("ZZ-9", "unknown"), ("N-02", "no-mark"),
        ("N-02", "refused"), ("T-13", "refused"), ("N-01", "applied"), ("N-02", "refused"), ("N-02", "refused"),
        ("N-03", "applied")]
    assert "a FAIL is never re-tested" in got[6].detail and "needs an ISO time" in got[1].detail
    assert "got ['go']" in got[8].detail and "got {'go': 1}" in got[9].detail
    assert lab.method(ctx, "N-01", tests=False).marks[0].by == "page"


def test_a_later_mark_in_the_index_is_not_overwritten_by_an_older_page_mark(ctx):
    lab.mark(ctx, "N-02", "drop", "user", "changed my mind")
    got = weekly.apply_marks(ctx, [_doc("N-02", "go", at="2026-10-04T07:30:00Z")])
    assert got[0].outcome == "superseded" and "drop by user" in got[0].detail
    assert lab.method(ctx, "N-02", tests=False).status == "dropped"


def test_marks_apply_oldest_first_so_the_newest_stands(ctx):
    weekly.apply_marks(ctx, [_doc("N-02", "drop", at="2026-10-05T07:40:00Z"), _doc("N-02", "go")])
    assert lab.method(ctx, "N-02", tests=False).status == "dropped"


def test_marks_must_be_a_list(ctx):
    with pytest.raises(InvalidQuery, match="JSON list"):
        weekly.apply_marks(ctx, {"N-02": "go"})


def test_a_note_edited_after_the_mark_does_not_reapply_it_over_a_later_one(ctx):
    weekly.apply_marks(ctx, [_doc("N-02", "go")])
    lab.mark(ctx, "N-02", "drop", "user", "changed my mind")
    later_note = _doc("N-02", "go", at="2026-10-05T09:00:00Z", mark_at="2026-10-05T07:30:00.000Z", note="more")
    got = weekly.apply_marks(ctx, [later_note])
    assert got[0].outcome == "already" and lab.method(ctx, "N-02", tests=False).status == "dropped"


def test_page_marks_never_pass_as_the_user_or_the_autopilot(ctx):
    weekly.apply_marks(ctx, [_doc("N-02", "go", by="user"), _doc("N-01", "go", by="a" * 36)])
    assert lab.method(ctx, "N-02", tests=False).marks[0].by == "page:user"
    n1 = lab.method(ctx, "N-01", tests=False).marks[0]
    assert (n1.by, n1.note) == ("page", f"(page viewer {'a' * 36})")
