import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def line(name, ret, cagr, fall, cash, trades):
    return {"name": name, "label": name, "ret": ret, "return": ret, "cagr": cagr, "max_fall": fall,
            "cash_share": cash, "trades": trades, "points": [["2008-01-02", 0.0], ["2026-09-30", ret]]}


def window(name, verdict, fall_cut, given_up):
    return {"window": name, "start": "2008-01-02", "end": "2026-09-30", "switches": 14, "fall_cut": fall_cut,
            "given_up": given_up, "verdict": verdict,
            "lines": [line("trend_overlay", 3.1, 0.081, -0.24, 0.18, 14), line("held", 4.2, 0.094, -0.51, 0.0, 0)],
            "crashes": [{"name": "2008", "start": "2008-01-02", "end": "2009-12-31", "trend_return": -0.05,
                         "held_return": -0.21, "trend_fall": -0.12, "held_fall": -0.51, "cash_share": 0.6}]}


RESULT = {"result": {"series": "^SP500TR", "label": "S&P 500 total return (USD)", "rule": "At each month end...",
                     "cash": "0: no T-bill series (^IRX) is stored in the research database", "cost": 0.001,
                     "choose": window("choose", None, 0.4, 0.002), "confirm": window("confirm", "FAIL", 0.53, 0.013),
                     "verdict": "FAIL", "passed": False, "first_day": "1988-01-04", "last_day": "2026-09-30",
                     "run_at": "2026-10-02T10:00:00+00:00", "notes": ["A month end is a month's last session."],
                     "beside": [], "id": 3}}


def run(body: str, result=RESULT):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/plan/trend"] = {json.dumps(result)};
        ANSWERS["POST /api/jobs/plan-trend"] = {{ id: 21, kind: "plan_trend", state: "queued", progress: null }};
        const {{ trendSection }} = await import({json.dumps((STATIC_DIR / "lib" / "trend.js").as_uri())});
        const section = trendSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_both_windows_the_crashes_and_the_verdict():
    text, verdict = run("""
        section.start(); await settle();
        const v = find(node, (n) => n.attrs?.["data-verdict"] != null);
        console.log(JSON.stringify([node.textContent, [v.textContent, v.className]]));
    """)
    assert "Choose 1988-2007" in text and "reported only" in text and "Confirm 2008 on" in text
    assert "+8.10%" in text and "-24.0%" in text and "14" in text and "18%" in text
    assert "53.0%" in text and "+1.30 points" in text
    assert "2008" in text and "-51.0%" in text and "60%" in text
    assert "no T-bill series" in text and "At each month end" in text
    assert verdict == ["FAIL", "down"]


def test_nothing_kept_says_so_and_run_queues_the_job():
    text, asked = run("""
        section.start(); await settle();
        button(node, "Run trend test").fire("click"); await settle();
        console.log(JSON.stringify([node.textContent, asked]));
    """, {"result": None})
    assert "Not run yet" in text
    assert "POST /api/jobs/plan-trend" in asked and "GET /api/plan/trend" in asked
