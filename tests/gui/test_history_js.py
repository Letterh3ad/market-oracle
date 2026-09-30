import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

HONDA = {"query": '"Honda"', "aliases": ["Honda"], "skip": False, "source": "seed", "note": None}
CODE = {"query": "", "aliases": [], "skip": True, "source": "default", "note": "a listing code, not a name headlines use"}
COVERAGE = {"start": "2022-09-01", "quarters": ["2022-Q3", "2022-Q4"], "windows_done": 4, "windows_left": 2,
            "caveat": "today's list taken back in time", "thin_below": 5, "noisy_below": 0.5,
            "last_run": {"stopped": "google_news refused: circuit open", "failures": ["x"], "finished_at": "2026-09-30T02:00:00Z",
                         "summary": "stopped early (google_news refused: circuit open): 40 requests"},
            "rows": [{"symbol": "HMC", "name": "Honda", "main": True, "of": [], "first_bar": "2021-09-01",
                      "thin": ["2022-Q3"], "noisy": [], "search": HONDA,
                      "cells": {"2022-Q4": {"news": 12, "filings": 2, "outlets": 7, "on_topic": 0.9}}},
                     {"symbol": "MAN.DE", "name": "MAN SE", "main": False, "of": ["HMC"], "first_bar": None,
                      "thin": ["2022-Q3"], "noisy": ["2022-Q4"], "search": HONDA | {"query": '"MAN"', "source": "default"},
                      "cells": {"2022-Q4": {"news": 100, "filings": 0, "outlets": 40, "on_topic": 0.1}}},
                     {"symbol": "SHK.BE", "name": "SHINKO EL.IND.LTD", "main": False, "of": ["HMC"], "first_bar": None,
                      "thin": [], "noisy": [], "search": CODE, "cells": {}}]}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/history/coverage"] = {json.dumps(COVERAGE)};
        ANSWERS["POST /api/jobs/history"] = {{ id: 3, kind: "history", state: "queued", progress: null }};
        ANSWERS["PUT /api/history/names/MAN.DE"] = {{ symbol: "MAN.DE", forgotten: 5 }};
        ANSWERS["DELETE /api/history/names/MAN.DE"] = {{ symbol: "MAN.DE", forgotten: 0 }};
        const {{ historySection }} = await import({json.dumps((STATIC_DIR / "lib" / "history.js").as_uri())});
        const section = historySection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_coverage_names_noise_and_why_the_last_run_stopped():
    text, red = run("""
        section.start(); await settle();
        const red = node.all((n) => n.tag === "td" && n.className.includes("down")).map((n) => n.textContent);
        console.log(JSON.stringify([node.textContent, red]));
    """)
    assert "3 companies from 2022-09-01; 4 news windows done, 2 windows or companies left" in text
    assert "2 company quarters are thin" in text and "1 noisy (under 50% of headlines name the company)" in text
    assert "Last run, 2026-09-30 02:00 UTC: stopped early (google_news refused" in text
    assert '"Honda" (seed)' in text and "not searched (a listing code" in text
    assert "12/2" in text and "map of HMC" in text
    assert red == ["0/0", "0/0", "100/0 noisy"]


def test_backfill_history_starts_the_job_for_the_picked_list_and_says_where_to_follow_it():
    asked, text = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Backfill history").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/history?watchlist=main"]
    assert "The history backfill is queued" in text and "Activity tab" in text


def test_a_company_is_picked_from_the_table_and_its_search_name_saved_or_cleared():
    asked, text = run("""
        section.start(); await settle();
        find(node, (n) => n.tag === "a" && n.textContent === "MAN.DE").fire("click");
        const picked = labelled(node, "Search name query").value;
        labelled(node, "Search name query").value = '"MAN Truck" OR "MAN SE"';
        labelled(node, "Search name aliases").value = "MAN, Traton";
        asked.length = 0;
        button(node, "Save search name").fire("click"); await settle();
        const saved = node.textContent;
        button(node, "Use default name").fire("click"); await settle();
        console.log(JSON.stringify([[picked, ...asked], saved + node.textContent]));
    """)
    assert asked[0] == '"MAN"'
    assert asked[1] == "PUT /api/history/names/MAN.DE"
    assert "GET /api/history/coverage?watchlist=main&fresh=true" in asked
    assert "DELETE /api/history/names/MAN.DE" in asked
    assert "MAN.DE: saved; 5 news windows will be asked again" in text and "back to the default name" in text
