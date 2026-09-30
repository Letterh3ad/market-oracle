import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

COVERAGE = {"start": "2022-09-01", "quarters": ["2022-Q3", "2022-Q4"], "windows_done": 4, "windows_left": 2,
            "caveat": "today's list taken back in time", "thin_below": 5,
            "rows": [{"symbol": "HMC", "name": "Honda", "main": True, "of": [], "first_bar": "2021-09-01",
                      "thin": ["2022-Q3"], "cells": {"2022-Q4": {"news": 12, "filings": 2, "outlets": 7}}},
                     {"symbol": "GM", "name": "General Motors", "main": False, "of": ["HMC"], "first_bar": None,
                      "thin": ["2022-Q3", "2022-Q4"], "cells": {}}]}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/history/coverage"] = {json.dumps(COVERAGE)};
        ANSWERS["POST /api/jobs/history"] = {{ id: 3, kind: "history", state: "queued", progress: null }};
        const {{ historySection }} = await import({json.dumps((STATIC_DIR / "lib" / "history.js").as_uri())});
        const section = historySection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_coverage_per_company_and_quarter_with_thin_ones_marked():
    text, thin = run("""
        section.start(); await settle();
        const red = node.all((n) => n.tag === "td" && n.className.includes("down")).map((n) => n.textContent);
        console.log(JSON.stringify([node.textContent, red]));
    """)
    assert "2 companies from 2022-09-01; 4 news windows done, 2 windows or companies left" in text
    assert "3 company quarters are thin" in text and "today's list taken back in time" in text
    assert "12/2" in text and "map of HMC" in text and "none" in text
    assert thin == ["0/0", "0/0", "0/0"]


def test_backfill_history_starts_the_job_for_the_picked_list_and_says_where_to_follow_it():
    asked, text = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Backfill history").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/history?watchlist=main"]
    assert "The history backfill is queued" in text and "Activity tab" in text
