import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

KR = {"code": "kospi200", "name": "KOSPI 200", "members": 200, "present": 190, "added": 4, "corrected": 1,
      "failed": 0, "waiting": 5, "fetched_at": "2026-09-24T12:00:00Z", "error": None}
SP = {**KR, "code": "sp500", "name": "S&P 500", "members": 0, "present": 0, "added": 0, "corrected": 0,
      "waiting": 0, "fetched_at": None, "error": "S&P 500: answered HTTP 404"}
REPORT = {"indexes": [KR, SP], "lookups": 12, "stopped": "Yahoo stopped answering: HTTP 429",
          "changes": [{"symbol": "373220.KS", "name": "LG Energy Solution, Ltd.", "index": "KOSPI 200",
                       "kind": "added", "note": "in the KOSPI 200; Large Cap"}]}


FUNDS = [{"symbol": "IAD.L", "name": "Invesco Asia Dragon Trust Ord", "index_code": "ftse250", "kept": None},
         {"symbol": "JMGI.L", "name": "JPMorgan Emerging Markets Growth & Income plc.", "index_code": "ftse250",
          "kept": "watched"}]


CORRECTIONS = {"rows": [{"symbol": "008110.KS", "kind": "corrected", "index_code": "kospi200",
                         "note": "not on Yahoo; the KOSPI 200 lists Samsung C&T as 028260.KS",
                         "fields": {"delisted": True}}],
               "stale": ["Gone plc (name:gone plc|United Kingdom|equity)"]}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS = {{
          "GET /api/universe/backfill": {json.dumps([KR, SP])},
          "GET /api/activity": {{ jobs: [], elsewhere: [] }},
          "POST /api/jobs/backfill": {{ id: 4, kind: "universe", state: "done", result: {json.dumps(REPORT)} }},
          "GET /api/universe/funds": {json.dumps({"lines": FUNDS, "removed": False})},
          "POST /api/universe/funds/remove": {json.dumps({"lines": FUNDS, "removed": True})},
          "GET /api/universe/corrections": {json.dumps(CORRECTIONS)},
          "DELETE /api/universe/corrections/008110.KS": {json.dumps(CORRECTIONS["rows"][0])},
        }};
        const {{ backfillSection }} = await import({json.dumps((STATIC_DIR / "lib" / "backfill.js").as_uri())});
        const banner = new Node("div");
        document.getElementById = () => banner;
        const node = backfillSection();
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_opening_it_shows_each_index_counts_without_running_anything():
    text, asked = run("""node.open = true; node.fire("toggle"); await settle();
        console.log(JSON.stringify([node.textContent, asked]));""")
    assert "KOSPI 2002001904105" in text
    assert "answered HTTP 404" in text
    assert "POST /api/jobs/backfill" not in asked


def test_the_button_runs_a_batch_and_shows_what_changed_and_why_it_stopped():
    text, asked = run("""button(node, "Backfill from indexes").fire("click"); await settle(); await settle();
        console.log(JSON.stringify([node.textContent, asked]));""")
    assert "POST /api/jobs/backfill" in asked
    assert "12 Yahoo lookups this run; stopped early: Yahoo stopped answering: HTTP 429" in text
    assert "LG Energy Solution, Ltd." in text and "in the KOSPI 200; Large Cap" in text


def test_investment_trusts_are_listed_first_and_removed_only_on_a_second_press():
    listed, removed, asked = run("""button(node, "Find investment trusts").fire("click"); await settle();
        const listed = node.textContent;
        button(node, "Remove 1").fire("click"); await settle();
        console.log(JSON.stringify([listed, node.textContent, asked]));""")
    assert "Invesco Asia Dragon Trust Ord" in listed and "kept: watched" in listed
    assert asked.count("POST /api/universe/funds/remove") == 1
    assert "Removed 1" in removed


def test_corrections_are_listed_with_stale_keys_and_each_can_be_undone():
    listed, undone, asked = run("""button(node, "Show corrections").fire("click"); await settle();
        const listed = node.textContent;
        button(node, "Undo").fire("click"); await settle();
        console.log(JSON.stringify([listed, node.textContent, asked]));""")
    assert "008110.KS" in listed and "delisted=true" in listed and "Samsung C&T as 028260.KS" in listed
    assert "Gone plc (name:gone plc|United Kingdom|equity)" in listed
    assert "DELETE /api/universe/corrections/008110.KS" in asked
    assert "Undid the correction of 008110.KS" in undone and "Refresh the universe" in undone
