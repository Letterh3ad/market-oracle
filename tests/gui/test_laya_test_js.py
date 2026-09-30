import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

SEGMENTS = [{"start": "2022-09-21", "end": "2023-03-20", "ic": None, "t": None, "days": 0, "counted": False,
             "why": "no trailing fit", "weights": {"own_mean": 0.0}},
            {"start": "2023-03-21", "end": "2023-09-20", "ic": 0.031, "t": 2.4, "days": 120, "counted": True,
             "why": None, "weights": {"own_mean": 0.7, "own_relevance": -0.3}}]
HORIZON = {"horizon": 5, "lookback": 7, "ic": 0.031, "t": 2.4, "days": 120, "observations": 5000, "segments": SEGMENTS,
           "features": [{"name": "own_mean", "ic": 0.02, "t": 1.1, "days": 120, "positive": 1, "in_score": True},
                        {"name": "own_sum", "ic": 0.01, "t": 0.5, "days": 110, "positive": 1, "in_score": False}],
           "verdict": "INCONCLUSIVE", "passed": False, "positive": 1, "counted": 1}
TEST = {"run": "main", "label": "main (links first known by the article's day)", "model": "laya@x",
        "start": "2022-09-21", "end": "2023-09-20", "companies": 51, "articles": {"own": 900, "competitor": 4000},
        "horizons": [HORIZON], "verdict": "INCONCLUSIVE", "passed": False, "run_at": "2026-09-30T23:00:00+00:00",
        "t_method": "Newey-West", "caveat": "Survivorship: today's main list", "id": 1}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/laya/tests"] = {json.dumps({"tests": [TEST], "last_scoring": {"summary": "finished: 51 groups"}})};
        ANSWERS["GET /api/laya/plan"] = {json.dumps({"pairs": 148009, "targets": 51, "unscored": 100, "ms_per_pair": 12.5,
                                                     "estimate_s": 7200, "basis": "measured"})};
        ANSWERS["POST /api/jobs/laya-test"] = {{ id: 4, kind: "laya_test", state: "done",
            result: {{ label: "sensitivity", verdict: "FAIL" }} }};
        const {{ layaTestSection }} = await import({json.dumps((STATIC_DIR / "lib" / "laya_test.js").as_uri())});
        const section = layaTestSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_the_kept_table_verdict_segments_left_out_and_the_caveat():
    [text] = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent]));
    """)
    assert "main (links first known by the article's day): INCONCLUSIVE" in text
    assert "no: no trailing fit" in text and "own_mean +0.70, own_relevance -0.30" in text
    assert "own_sum (secondary)" in text and "Last scoring run: finished: 51 groups." in text
    assert "Survivorship" in text


def test_the_sensitivity_button_starts_that_run_and_the_estimate_reads_the_plan():
    asked, text = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Run sensitivity (links as known today)").fire("click"); await settle();
        button(node, "Count pairs and estimate").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked[0] == "POST /api/jobs/laya-test?sensitivity=true" and "GET /api/laya/plan" in asked
    assert "sensitivity: FAIL" in text and "about 2.0 h at 12.5 ms a pair" in text
