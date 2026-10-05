"""The weekly page generator (research-lab 08): candidates picked from the results table JSON v1, and one
self-contained page with every value escaped. No network, no database."""

import copy
import json
import re
from pathlib import Path

import pytest

from oracle.lab import weekly_page as wp

SNAPSHOT = Path(__file__).parent / "snapshots" / "results_table.json"
AT = "2026-10-05T07:30:00+00:00"


def _candidate(ref, rating, quality, status="new", **kw):
    row = copy.deepcopy(next(r for r in json.loads(SNAPSHOT.read_text(encoding="utf-8"))["rows"] if r["ref"] == "A-01"))
    row.update(ref=ref, name=kw.pop("name", f"Method {ref}"), status=status, rating=rating, quality=quality, notes=[],
               sources=[] if quality is None else row["sources"][:1], **kw)
    return row


@pytest.fixture
def table():
    t = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    t["rows"] += [_candidate("C-01", 2, "replicated"), _candidate("C-02", 7, "peer-reviewed"),
                  _candidate("C-03", 7, "replicated"), _candidate("C-04", None, None),
                  _candidate("C-05", 5, "practitioner"), _candidate("C-06", 4, "abstract-only"),
                  _candidate("C-07", 9, "replicated", status="proposed"),
                  _candidate("C-08", 9, "replicated", status="dropped", verdict="dropped"),
                  _candidate("P-01", 9, "replicated", family="F. UK wrappers, tax and behaviour"),
                  _candidate("P-02", 9, "replicated", family="ai-track (superseded tickets)")]
    return t


def test_candidates_are_undecided_methods_by_rating_then_quality(table):
    got = wp.pick_candidates(table)
    assert [c.ref for c in got] == ["C-07", "C-03", "C-02", "C-05", "C-06"]
    assert [c.ref for c in wp.pick_candidates(table, 10)] == ["C-07", "C-03", "C-02", "C-05", "C-06", "C-01", "C-04"]
    assert "A-01" not in {c.ref for c in wp.pick_candidates(table, 10)}, "a go mark is a decision already"
    assert not {"P-01", "P-02"} & {c.ref for c in wp.pick_candidates(table, 10)}, "plan and tool families"


def test_each_candidate_carries_a_recommendation_from_its_rating_and_evidence(table):
    rec = {c.ref: c.recommendation for c in wp.pick_candidates(table, 10)}
    assert rec["C-07"].startswith("go: rated 9, replicated evidence")
    assert rec["C-01"].startswith("drop: rated 2")
    assert rec["C-05"].startswith("dig deeper: only a practitioner source")
    assert rec["C-06"].startswith("dig deeper: no source beyond an abstract")
    assert rec["C-04"].startswith("dig deeper: no source beyond an abstract")


def test_the_page_has_every_section_and_a_card_per_candidate(table):
    cands = wp.pick_candidates(table)
    papers = [wp.Paper("Returns to buying winners", "Jegadeesh and Titman", 1993, "https://doi.org/10.1/x",
                       "Unpaywall: closed", "library")]
    html = wp.render_page(table, papers, cands, ["Widen to mid caps?"], AT)
    assert "<title>Research Lab Weekly</title>" in html
    for section in ("candidates", "results", "deaths", "papers", "questions"):
        assert f'<section id="{section}">' in html
    assert re.findall(r'data-key="([^"]+)"', html) == [c.ref for c in cands]
    marks = re.findall(r'<button type="button" data-mark="([a-z]+)"', html)
    assert marks == ["go", "drop", "dig"] * 5
    assert html.count("<textarea") == 5 and "Recommendation: go: rated 9" in html
    assert html.count("<tr>") == len(table["rows"]) + 1
    assert "momentum_12_1</span> (signal, bar t &gt; 2.00): fail" in html
    assert "h20 IC train +0.0123 confirm +0.0071 t 0.45" in html
    assert '<span class="stage">confirm</span>' in html and "5 deaths among 20 rows." in html
    assert '<a href="https://doi.org/10.1/x"' in html and "Jegadeesh and Titman, 1993" in html
    assert "<li>Widen to mid caps?</li>" in html
    assert "Generated 2026-10-05 07:30 UTC" in html


def test_the_page_is_self_contained_and_themed(table):
    html = wp.render_page(table, [], wp.pick_candidates(table), [], AT)
    assert not re.search(r"<script[^>]+src=|<link\b|@import|https?://[^\"']*\.(js|css)\b", html)
    assert ':root{--bg:' in html and '@media (prefers-color-scheme: dark){:root:not([data-theme="light"])' in html
    assert ':root[data-theme="dark"]{' in html and "body{background:var(--bg)" in html
    assert 'name="viewport"' in html and ".scroll{overflow-x:auto" in html and "overflow-x:hidden" in html
    script = html.split("<script>")[1].split("</script>")[0]
    assert "innerHTML" not in script and "window.claude.db" not in script
    assert 'claude.use("db")' in script and 'collection("marks")' in script and script.count("onSnapshot(") == 1


def test_every_value_is_escaped(table):
    evil = "<script>alert(1)</script>"
    row = _candidate("C-99", 9, "replicated", name=evil, family='a"b', evidence="x & <b>y</b>")
    row["notes"] = [{"mark": "go", "by": "<i>me</i>", "at": AT, "note": "<img src=x>"}]
    table["rows"].append(row)
    table["rows"][0]["tests"] = [{"key": "<k>", "label": "", "kind": "signal", "registered_on": "", "bar": "t<2",
                                  "legacy": True, "commit_hash": None, "note": "", "verdict": "<v>", "lines": []}]
    papers = [wp.Paper(evil, "<a>", None, "javascript:alert(1)", "<r>", "<n>")]
    html = wp.render_page(table, papers, wp.pick_candidates(table), [evil], AT)
    body = html.split("<script>")[0]
    assert evil not in body and "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    for raw in ("<img src=x>", "<i>me</i>", "<b>y</b>", "<k>", "<v>", "<r>", "<n>", 'a"b'):
        assert raw not in body
    assert 'href="javascript' not in body, "only http and https sources are linked"


def test_empty_sections_say_so(table):
    table = {**table, "rows": [], "deaths": {k: 0 for k in table["deaths"]}, "verdicts": {}}
    html = wp.render_page(table, [], [], ["  "], AT)
    assert "No methods or tests in the table yet." in html and "No method has died yet." in html
    assert "No undecided candidates" in html and "None wanted" in html and "No open questions this week." in html
    assert 'id="db-state"' not in html


def test_another_table_version_is_refused(table):
    with pytest.raises(ValueError, match="version 1"):
        wp.render_page({**table, "version": 2}, [], [], [], AT)
    with pytest.raises(ValueError, match="version 1"):
        wp.pick_candidates({**table, "version": None})


def test_the_capabilities_the_page_needs():
    assert wp.CAPABILITIES == {"db": {"rules": [{"path": "marks", "read": "view", "write": "interact"}]}, "user": {}}
