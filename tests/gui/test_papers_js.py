import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE
from tests.gui.test_literature_js import SOURCE, STATUS_FIELD
from tests.gui.test_plan_summary_js import SENT

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

WANTED = {"source": SOURCE, "reason": "Unpaywall: closed (no open copy); Semantic Scholar: no open copy",
          "note": "library", "at": "2026-10-04T09:00:00+00:00", "by": "autopilot"}
COPY = {"url": "https://example.org/p.pdf", "via": "unpaywall", "licence": "cc-by", "version": "publishedVersion"}


def run(body: str):
    script = f"""
        {PRELUDE}
        {STATUS_FIELD}
        {SENT}
        ANSWERS["GET /api/lab/papers/wanted"] = {{ wanted: [{json.dumps(WANTED)}], inbox: "C:/lab/inbox" }};
        ANSWERS["POST /api/lab/papers/fetch"] = {{ source: {json.dumps(SOURCE)}, copy: {json.dumps(COPY)}, pages: 31,
          chars: 90000, wanted: false, note: "unpaywall: read https://example.org/p.pdf" }};
        ANSWERS["POST /api/lab/papers/wanted"] = {json.dumps(WANTED)};
        ANSWERS["DELETE /api/lab/papers/wanted"] = {{ removed: "#7" }};
        ANSWERS["POST /api/lab/papers/inbox"] = {{ folder: "C:/lab/inbox", results: [
          {{ file: "jt.pdf", source: {json.dumps(SOURCE)}, matched: "id doi:10.1/x", pages: 12, chars: 50000,
             note: "kept as the full text of #7" }},
          {{ file: "odd.pdf", source: null, matched: "", pages: 2, chars: 900, note: "no recorded source matches" }}] }};
        const {{ papersSection }} = await import({json.dumps((STATIC_DIR / "lib" / "papers.js").as_uri())});
        const section = papersSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8",
                         check=True)
    return json.loads(out.stdout)


def test_the_card_lists_papers_i_would_like_and_the_inbox_folder():
    text, asked_ = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent, asked]));
    """)
    assert "#7 52-Week High" in text and "Unpaywall: closed" in text and "note: library" in text
    assert "2026-10-04 by autopilot" in text and "C:/lab/inbox" in text and asked_ == ["GET /api/lab/papers/wanted"]


def test_fetch_want_remove_and_scan_send_their_requests():
    sent_, asked_, text = run("""
        section.start(); await settle();
        labelled(node, "Paper source").value = "10.1/x";
        button(node, "Fetch open copy").fire("click"); await settle();
        const fetched = node.textContent;
        labelled(node, "Wanted note").value = "library";
        button(node, "Add to papers I'd like").fire("click"); await settle();
        button(node, "Remove").fire("click"); await settle();
        button(node, "Scan inbox").fire("click"); await settle();
        console.log(JSON.stringify([sent, asked, fetched + "|" + node.textContent]));
    """)
    assert ["/api/lab/papers/fetch", {"source": "10.1/x"}] in sent_
    assert ["/api/lab/papers/wanted", {"source": "10.1/x", "note": "library"}] in sent_
    assert "DELETE /api/lab/papers/wanted?source=%237" in asked_ and "POST /api/lab/papers/inbox" in asked_
    assert "full text kept, 31 pages from unpaywall" in text
    assert "jt.pdf: #7 52-Week High (by id doi:10.1/x, 12 pages)" in text and "odd.pdf: no recorded source matches" in text
    assert "Inbox scanned: 1 of 2 matched." in text


def test_fetch_again_from_a_row_names_the_source_by_number():
    sent_ = run("""
        section.start(); await settle();
        button(node, "Fetch again").fire("click"); await settle();
        console.log(JSON.stringify(sent));
    """)
    assert sent_ == [["/api/lab/papers/fetch", {"source": "#7"}]]
