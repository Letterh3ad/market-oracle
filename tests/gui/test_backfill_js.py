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


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS = {{
          "GET /api/universe/backfill": {json.dumps([KR, SP])},
          "GET /api/activity": {{ jobs: [], elsewhere: [] }},
          "POST /api/jobs/backfill": {{ id: 4, kind: "universe", state: "done", result: {json.dumps(REPORT)} }},
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
