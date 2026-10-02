import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def row(key, label, ret, cagr, compared=True):
    return {"key": key, "name": key, "label": f"{label} (proxy)", "source": f"proxy: X ({label})",
            "kind": "total return", "ret": ret, "return": ret, "cagr": cagr, "max_fall": -0.34, "ter": 0.0019,
            "ter_drag": 0.0043, "us_share": 0.62, "compared": compared, "own_start": "2008-03-28", "own_ret": 3.0,
            "own_cagr": 0.091, "own_fall": -0.41, "trades": 0,
            "points": [["2012-01-12", 0.0], ["2026-09-30", ret]]}


RESULT = {"result": {
    "base": "GBP", "start": "2012-01-12", "end": "2026-09-30", "checked": "2026-10-03",
    "run_at": "2026-10-03T10:00:00+00:00", "notes": ["Every line is a proxy modelled as the fund."], "id": 4,
    "rows": [row("all_world", "All-world", 2.5, 0.089), row("uk", "UK shares (part of the mix)", 0.9, 0.044, False)],
    "tracking": [{"key": "all_world", "real": "VWRP.L", "real_label": "Vanguard FTSE All-World UCITS ETF Acc (VWRP)",
                  "proxy": "ACWI", "start": "2019-07-23", "end": "2026-09-30", "real_return": 0.9,
                  "model_return": 0.88, "difference": 0.02, "yearly_difference": 0.0021},
                 {"key": "uk", "real": "VUKG.L", "real_label": "Vanguard FTSE 100 UCITS ETF Acc (VUKG)",
                  "proxy": "EWU", "start": None, "end": None, "real_return": None, "model_return": None,
                  "difference": None, "yearly_difference": None}],
    "fx": {"day": "2012-01-12", "symbol": "ACWI", "close": 43.21, "currency": "USD", "rate": 1.5321,
           "close_base": 28.2}}}


def run(body: str, result=RESULT):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/plan/funds"] = {json.dumps(result)};
        ANSWERS["POST /api/jobs/plan-funds"] = {{ id: 22, kind: "plan_funds", state: "queued", progress: null }};
        const {{ fundsSection }} = await import({json.dumps((STATIC_DIR / "lib" / "funds.js").as_uri())});
        const section = fundsSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_each_fund_as_a_proxy_the_tracking_and_the_fx_check():
    text = run("section.start(); await settle(); console.log(JSON.stringify(node.textContent));")
    assert "Every line is a proxy." in text and "All-world (proxy)" in text and "UK shares (part of the mix)" in text
    assert "2012-01-12 to 2026-09-30" in text and "+8.90%" in text and "-34.0%" in text and "0.19%" in text
    assert "+0.43" in text and "62%" in text and "since 2008-03-28" in text
    assert "VWRP.L" in text and "2019-07-23 to 2026-09-30" in text and "+0.21" in text
    assert "No stored days inside the proxy's." in text
    assert "1.5321 USD per GBP = 28.20 GBP" in text


def test_start_loads_once_and_a_failed_load_is_cleared_by_a_good_one():
    asked, first, second = run("""
        const good = ANSWERS["GET /api/plan/funds"];
        ANSWERS["GET /api/plan/funds"] = { status: 500, error: "database is locked" };
        section.start(); section.start(); await settle();
        const note = () => find(node, (n) => (n.className ?? "").includes("form-note"));
        const bad = note().textContent;
        ANSWERS["GET /api/plan/funds"] = good;
        await section.reload(); await settle();
        console.log(JSON.stringify([asked.filter((a) => a === "GET /api/plan/funds").length, bad,
          node.textContent.includes("VWRP.L")]));
    """)
    assert asked == 2 and "Could not load the fund comparison" in first and second is True


def test_nothing_kept_says_so_and_compare_queues_the_job():
    text, asked = run("""
        section.start(); await settle();
        button(node, "Compare funds").fire("click"); await settle();
        console.log(JSON.stringify([node.textContent, asked]));
    """, {"result": None})
    assert "Not run yet" in text and "POST /api/jobs/plan-funds" in asked
