import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_ledger_research_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

SETTINGS = {"enabled": True, "per_tick": 1, "max_total": 10, "never": ["Tesla"]}


def run(body: str):
    script = f"""
        {PRELUDE}
        document.getElementById = () => ({{ hidden: true, textContent: "" }});
        const m = await import({json.dumps((STATIC_DIR / "lib" / "discovery.js").as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_summary_says_what_was_added_skipped_and_could_not_be_used():
    result = {"note": None, "seeds": ["7203.T"], "source_errors": ["news for 7203.T from GDELT: busy"],
              "added": [{"symbol": "6902.T", "name": "DENSO Corporation", "why": "same industry as 7203.T"}],
              "skipped": [{"name": "Tesla, Inc.", "reason": "on the never list"}]}
    out = run(f"console.log(JSON.stringify(m.discoverySummary({json.dumps(result)})));")
    assert out == ["Added 6902.T (DENSO Corporation): same industry as 7203.T.",
                   "Skipped Tesla, Inc.: on the never list.", "Could not use news for 7203.T from GDELT: busy."]


def test_the_summary_explains_an_empty_run():
    out = run("""console.log(JSON.stringify([
        m.discoverySummary({ note: "the discovered list is full (10 of 10)" }),
        m.discoverySummary({ note: null, seeds: ["AAPL"], added: [], skipped: [], source_errors: [] })]));""")
    assert out == [["Nothing to do: the discovered list is full (10 of 10)."],
                   ["Nothing new to add this time (looked around AAPL)."]]


def test_an_empty_list_says_what_discover_now_does():
    out = run(f"""console.log(JSON.stringify(m.discoveryTable({{ settings: {json.dumps(SETTINGS)}, items: [] }},
        () => {{}}).textContent));""")
    assert out.startswith("Nothing discovered yet.") and "up to 1 a run, 10 in all" in out


def test_each_row_says_why_links_its_headlines_and_its_buttons_move_or_remove_it():
    body = {"settings": SETTINGS, "items": [{
        "symbol": "6752.T", "name": "Panasonic", "why": "named as a partner of 7203.T in the news",
        "added_at": "2026-09-24T12:00:00+00:00",
        "evidence": [{"title": "Toyota and Panasonic expand", "link": "https://news.test/1", "outlet": "Wire"},
                     {"title": "Bad link", "link": "javascript:alert(1)", "outlet": None}]}]}
    out = run(f"""
        let changes = 0;
        const table = m.discoveryTable({json.dumps(body)}, () => changes++);
        const links = table.all((n) => n.tag === "a").map((a) => a.attrs.href);
        const buttons = table.all((n) => n.tag === "button");
        for (const b of buttons) {{ b.fire("click"); await settle(); }}
        console.log(JSON.stringify({{ text: table.textContent, links, labels: buttons.map((b) => b.textContent),
          asked, changes }}));
    """)
    assert "named as a partner of 7203.T in the news" in out["text"] and "2026-09-24" in out["text"]
    assert out["links"] == ["#company/6752.T", "https://news.test/1"], "only web links are followed"
    assert out["labels"] == ["Move to my watchlist", "Remove"]
    assert out["asked"] == ["POST /api/discovery/6752.T/move", "DELETE /api/discovery/6752.T"]
    assert out["changes"] == 2
