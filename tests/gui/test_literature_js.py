import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE
from tests.gui.test_plan_summary_js import SENT

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

SOURCE = {"id": 7, "title": "52-Week High", "authors": "George, Hwang", "year": 2004, "quality": "peer-reviewed",
          "url": "https://example.org/52wk", "read_on": None, "note": "", "added_at": "x", "added_by": "seed",
          "keys": ["url:example.org/52wk"], "methods": ["T-15"]}
METHOD = {"ref": "T-15", "name": "52-week-high proximity", "family": "A. Cross-sectional stock anomalies",
          "definition": "stocks near their 52-week high keep rising.", "aliases": [], "status": "new",
          "duplicate_of": None, "origin": "archive", "rating": 3, "archive_status": "Untested",
          "evidence": "George-Hwang 2004: 0.45%/month.", "why": "momentum family", "result": "", "see_also": ["T-13"],
          "test_key": None, "status_by": "seed", "status_at": "2026-10-03T11:00:00+00:00", "added_at": "x",
          "sources": [SOURCE], "marks": []}
MARKED = {**METHOD, "status": "go", "status_by": "user", "status_at": "2026-10-03T12:00:00+00:00",
          "marks": [{"mark": "go", "status_from": "new", "status_to": "go", "by": "user",
                     "at": "2026-10-03T12:00:00+00:00", "note": "cheap"}]}
HIT = {"ref": "T-15", "name": "52-week-high proximity", "family": "A", "status": "new", "rating": 3, "score": 0.91,
       "matched": "name"}
LOOKUP = {"query": "52 week high", "key": None, "sources": [{"source": SOURCE, "score": 0.88}], "methods": [HIT],
          "note": "fuzzy match on titles, names and refs"}
SEARCH = {"id": 3, "query": "decay", "place": "SSRN", "at": "2026-10-03T12:00:00+00:00", "by": "autopilot",
          "note": "one hit", "found": [SOURCE]}
METHODS = {"methods": [{k: v for k, v in METHOD.items() if k not in ("sources", "marks")}],
           "statuses": ["new", "go", "built"], "marks": ["dig", "drop", "go"]}


# A method's own "status" field is not an HTTP status: only a number fails the request here.
STATUS_FIELD = """
globalThis.fetch = async (url, options) => {
  asked.push(`${options?.method ?? "GET"} ${url}`);
  const body = ANSWERS[`${options?.method ?? "GET"} ${url.split("?")[0]}`] ?? {};
  if (typeof body.status === "number") return { ok: false, status: body.status, statusText: "", json: async () => ({ error: body.error }) };
  return { ok: true, status: 200, json: async () => body };
};
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        {STATUS_FIELD}
        {SENT}
        ANSWERS["GET /api/lab/methods"] = {json.dumps(METHODS)};
        ANSWERS["GET /api/lab/searches"] = {{ searches: [{json.dumps(SEARCH)}] }};
        ANSWERS["GET /api/lab/lookup"] = {json.dumps(LOOKUP)};
        ANSWERS["GET /api/lab/methods/T-15"] = {json.dumps(METHOD)};
        ANSWERS["POST /api/lab/methods/T-15/mark"] = {json.dumps(MARKED)};
        ANSWERS["POST /api/lab/sources"] = {{ source: {json.dumps(SOURCE)}, created: true,
          method: {{ ...{json.dumps(METHOD)}, ref: "M-001" }}, nearest: [{json.dumps(HIT)}] }};
        ANSWERS["POST /api/lab/searches"] = {json.dumps(SEARCH)};
        const {{ literatureSection }} = await import({json.dumps((STATIC_DIR / "lib" / "literature.js").as_uri())});
        const section = literatureSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8",
                         check=True)
    return json.loads(out.stdout)


def test_the_card_lists_methods_with_status_and_the_search_log():
    text, asked_ = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent, asked]));
    """)
    assert "1 methods" in text and "52-week-high proximity" in text and "A. Cross-sectional stock anomalies" in text
    assert '2026-10-03 12:00 SSRN: "decay" by autopilot (one hit)' in text and "George, Hwang 2004, 52-Week High" in text
    assert "GET /api/lab/methods" in asked_ and "GET /api/lab/searches" in asked_


def test_lookup_shows_sources_and_nearest_methods_and_opens_one():
    text, asked_ = run("""
        section.start(); await settle();
        labelled(node, "Look up").value = "52 week high";
        button(node, "Look up").fire("click"); await settle();
        const after = node.textContent;
        button(node, "T-15").fire("click"); await settle();
        console.log(JSON.stringify([after + "|" + node.textContent, asked]));
    """)
    assert "GET /api/lab/lookup?q=52%20week%20high" in asked_ and "GET /api/lab/methods/T-15" in asked_
    assert "0.88 " in text and "0.91" in text and "fuzzy match" in text
    assert "Rule: stocks near their 52-week high keep rising." in text and "status new (by seed" in text


def test_a_status_filter_asks_for_that_status():
    asked_ = run("""
        section.start(); await settle();
        const pick = labelled(node, "Status");
        pick.value = "built"; pick.fire("change"); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert "GET /api/lab/methods?status=built" in asked_


def test_marking_sends_the_mark_and_shows_who_and_when():
    sent_, text = run("""
        await section.open("T-15"); await settle();
        labelled(node, "Mark note").value = "cheap";
        button(node, "Go").fire("click"); await settle();
        console.log(JSON.stringify([sent, node.textContent]));
    """)
    assert sent_ == [["/api/lab/methods/T-15/mark", {"how": "go", "note": "cheap"}]]
    assert "T-15 is now go." in text and "2026-10-03 12:00 go: new → go by user: cheap" in text


def test_recording_and_logging_send_their_fields():
    sent_, text = run("""
        labelled(node, "Source id or URL").value = "doi:10.1/x";
        labelled(node, "Title").value = "A paper";
        labelled(node, "Year").value = "2024";
        labelled(node, "Quality").value = "working-paper";
        labelled(node, "Yields methods").value = "T-15, A-08";
        labelled(node, "New method").value = "Fresh idea";
        labelled(node, "New method aliases").value = "fresh, new one";
        button(node, "Record").fire("click"); await settle();
        labelled(node, "Search query").value = "decay";
        labelled(node, "Searched where").value = "SSRN";
        labelled(node, "Found sources").value = "doi:10.1/x";
        button(node, "Log search").fire("click"); await settle();
        console.log(JSON.stringify([sent, node.textContent]));
    """)
    assert sent_[0] == ["/api/lab/sources", {
        "id": "doi:10.1/x", "title": "A paper", "authors": None, "year": 2024, "quality": "working-paper", "note": None,
        "read_on": None, "methods": ["T-15", "A-08"], "new_method": "Fresh idea", "family": "", "definition": "",
        "aliases": ["fresh", "new one"]}]
    assert sent_[1] == ["/api/lab/searches", {"query": "decay", "where": "SSRN", "found": ["doi:10.1/x"], "note": ""}]
    assert "Logged search #3 (1 found)." in text and "Nearest methods (check for a duplicate)" in text


def test_a_refused_mark_says_why():
    text = run("""
        ANSWERS["POST /api/lab/methods/T-15/mark"] = { status: 400, error: "T-15 has a verdict (fail)" };
        await section.open("T-15"); await settle();
        button(node, "Drop").fire("click"); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "T-15 has a verdict (fail)" in text
