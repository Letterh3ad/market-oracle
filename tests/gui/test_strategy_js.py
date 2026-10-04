import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

MODEL = {"platform": "flat", "percent_fee": 0.25, "percent_cap": 0, "flat_fee": 9.99, "dealing_fee": 5.0, "fx_fee": 0.5,
         "stamp_duty": 0.005, "half_spreads": [["Mega Cap", 0.001], ["Small Cap", 0.005]], "default_half_spread": 0.001,
         "pot": 20000, "notes": ["Stamp duty 0.5% on buys of UK shares only (UK-listed ETFs are exempt)."]}
TRADE = {"value": 10000.0, "side": "buy", "dealing": 5.0, "spread": 10.0, "stamp": 50.0, "fx": 0.0, "total": 65.0}
"""The route's shape: test_a_trade_priced_by_the_real_route_shows feeds the real one."""


def line(name, label, cagr):
    return {"name": name, "label": label, "ret": cagr * 2, "cagr": cagr, "max_fall": 0.2, "sharpe": 0.8, "trades": 3,
            "turnover": 0.15, "dealing": 15.0, "spread": 20.0, "stamp": 0.0, "fx": 30.0, "platform": 40.0, "points": []}


def window(name, verdict):
    return {"window": name, "start": "2020-01-02", "end": "2026-10-02", "strategy": line("strategy", "S after costs", 0.1),
            "gross": line("gross", "S before costs", 0.11), "index": line("index", "Index after costs", 0.09),
            "index_gross": line("index_gross", "Index before costs", 0.092), "excess": 0.01, "gross_excess": 0.018,
            "dsr": 0.4321, "verdict": verdict}


RESULT = {"key": "s1", "label": "S", "rule": "hold the best", "bar_rule": "beats the index after UK retail costs",
          "variants": 3, "pot": 20000, "costs": MODEL, "train": window("train", None), "confirm": window("confirm", "FAIL"),
          "verdict": "FAIL", "passed": False, "first_day": "2010-01-04", "last_day": "2026-10-02",
          "run_at": "2026-10-04T02:00:00+00:00", "notes": ["Cash earns nothing."], "id": 7}
CHECK = {"recorded_verdict": "FAIL", "run_at": "2026-10-02T12:00:00+00:00", "last_day": "2026-10-01", "matches": True,
         "note": "A check on the strategy engine, not a new test.",
         "rows": [{"window": "confirm", "measure": "CAGR", "recorded": 0.0801, "reproduced": 0.08011, "matches": True},
                  {"window": "confirm", "measure": "switches", "recorded": 17, "reproduced": 17, "matches": True}]}


def run(body: str, costs=None):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/lab/costs"] = {json.dumps(costs or {"model": MODEL, "trade": TRADE})};
        ANSWERS["GET /api/lab/strategies"] = {json.dumps({"strategies": [{"key": "s1", "label": "S", "result": RESULT}],
                                                          "kept": []})};
        ANSWERS["POST /api/jobs/strategy-test"] = {{ id: 21, kind: "strategy_test", state: "queued" }};
        ANSWERS["GET /api/activity/21"] = {{ id: 21, kind: "strategy_test", state: "done", result: null }};
        ANSWERS["GET /api/lab/trend-check"] = {json.dumps(CHECK)};
        const {{ strategySection }} = await import({json.dumps((STATIC_DIR / "lib" / "strategy.js").as_uri())});
        const section = strategySection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_the_cost_model_shows_and_a_hand_trade_is_priced():
    asked, text = run("""
        await section.start(); await settle();
        labelled(node, "UK shares (stamp duty on buys)").checked = true;
        labelled(node, "Size band").value = "Small Cap";
        button(node, "Price trade").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert "GET /api/lab/costs?value=10000&side=buy&currency=GBP&uk_shares=true&band=Small+Cap" in asked
    assert "Dealing £5.00 a trade; FX 0.50% a trade on non-GBP listings; stamp duty 0.5% on UK share buys" in text
    assert "Small Cap 0.50%" in text and "pot of £20,000.00" in text and "UK-listed ETFs are exempt" in text
    assert "A buy of £10,000.00: dealing £5.00, spread £10.00, stamp duty £50.00, FX £0.00; total £65.00." in text


def test_each_strategy_shows_its_kept_result_and_runs_as_a_job():
    asked, text = run("""
        await section.start(); await settle();
        asked.length = 0;
        button(node, "Run test").fire("click"); await settle(1700);
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/strategy-test?key=s1", "GET /api/activity/21", "GET /api/lab/strategies"]
    assert "The s1 strategy test finished." in text
    assert "excess CAGR after costs +1.00% (before +1.80%), deflated Sharpe 0.432: FAIL" in text
    assert "Train 2020-01-02 to 2026-10-02 (reported only)" in text and "S before costs" in text
    assert "dealing £15.00, spread £20.00, stamp £0.00, FX £30.00, platform £40.00" in text


def test_the_trend_overlay_check_compares_recorded_and_reproduced():
    text = run("""
        await section.start(); await settle();
        button(node, "Check the trend overlay").fire("click"); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "verdict FAIL as recorded. Reproduced within rounding." in text
    assert "+8.0100%" not in text and "8.0100%" in text and "8.0110%" in text and "17" in text


async def test_a_trade_priced_by_the_real_route_shows(client):
    real = (await client.get("/api/lab/costs", params={"value": 10000, "uk_shares": "true", "band": "Small Cap"})).json()
    assert set(real["trade"]) == set(TRADE), "the mock above has the route's shape"
    text = run("""
        await section.start(); await settle();
        button(node, "Price trade").fire("click"); await settle();
        console.log(JSON.stringify(node.textContent));
    """, real)
    assert "A buy of £10,000.00: dealing £0.00, spread £50.00, stamp duty £50.00, FX £0.00; total £100.00." in text
