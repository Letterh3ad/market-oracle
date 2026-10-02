import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

SETTINGS = {"settings": [{"section": "plan", "key": "platform", "value": "flat"},
                         {"section": "plan", "key": "percent_fee", "value": 0.25},
                         {"section": "plan", "key": "flat_fee", "value": 4.99},
                         {"section": "schedule", "key": "time", "value": "22:30"}]}
FEES = {"monthly": 500, "years": 20, "growth": 0.05, "start": 0, "contributed": 120000, "crossover": 23952.0,
        "crossover_year": 4, "using": "flat", "notes": ["Growth is an assumption."],
        "lines": [{"name": "none", "label": "No fees", "value": 200000, "fees": 0, "drag": 0, "yearly": []},
                  {"name": "percentage", "label": "Percentage 0.25% a year", "value": 196000, "fees": 3000,
                   "drag": 4000, "yearly": []},
                  {"name": "flat", "label": "Flat £4.99 a month", "value": 197500, "fees": 1198, "drag": 2500,
                   "yearly": []}]}
SPREAD = {"count": 200, "wins": 0.68, "median": 0.03, "p10": -0.04, "p90": 0.12, "worst": -0.2, "best": 0.3}
MIX = {"value": 150000, "contributed": 120000, "fees": 10, "trades": 4, "rebalanced": [], "weight": 0.75,
       "worst_fall": 0.3, "growth": 2.5}
TESTS = {"series": {"symbol": "ACWI", "label": "ACWI total return in GBP (proxy)", "kind": "total return",
                    "points": [["2008-04-30", 1.0], ["2026-09-30", 4.0]]},
         "fees": "flat £4.99 a month", "amount": 6000, "monthly": 500, "years": 10,
         "starts": [["2008-04-30", 0.05], ["2008-05-30", -0.02]], "lump": SPREAD, "windows": SPREAD,
         "window_count": 100, "second": {"symbol": "ISF.L", "label": "ISF.L total return in GBP"}, "weight": 0.75,
         "mixes": [{"rule": "none", "result": MIX}, {"rule": "yearly", "result": MIX},
                   {"rule": "5-point bands", "result": MIX}], "mix_span": ["2008-04-30", "2026-09-30"],
         "cash": {"share": 0.05, "cash_rate": 0.0, "invested": 4.0, "held": 3.8, "given_up": 0.004},
         "notes": ["After your flat platform's fees."]}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/settings"] = {json.dumps(SETTINGS)};
        ANSWERS["GET /api/plan/fees"] = {json.dumps(FEES)};
        ANSWERS["GET /api/plan/contributions"] = {json.dumps(TESTS)};
        ANSWERS["PUT /api/settings"] = {{}};
        const {{ planMoneySection }} = await import({json.dumps((STATIC_DIR / "lib" / "plan_money.js").as_uri())});
        const section = planMoneySection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_platform_form_shows_the_plan_settings_and_saves_each():
    asked, values = run("""
        section.start(); await settle();
        const named = (n) => find(node, (x) => x.attrs?.name === n);
        const shown = [named("platform").value, named("percent_fee").value, named("flat_fee").value, named("fx_fee").value];
        named("fx_fee").value = "0.5";
        button(node, "Save platform").fire("click"); await settle();
        console.log(JSON.stringify([asked.filter((a) => a.startsWith("PUT")).length, shown]));
    """)
    assert asked == 6
    assert values == ["flat", "0.25", "4.99", "0"]


def test_the_fee_calculator_shows_the_crossover_and_every_platform():
    text = run("""
        section.start(); await settle();
        button(node, "Work out fees").fire("click"); await settle();
        console.log(JSON.stringify([node.textContent, asked.find((a) => a.includes("/api/plan/fees"))]));
    """)
    shown, url = text
    assert "Flat beats percentage once the pot is above " in shown and "23,952" in shown and "year 4" in shown
    assert "4.99 a month (yours)" in shown and "No fees" in shown
    assert "monthly=500" in url and "years=25" in url and "growth=5" in url


def test_the_contribution_tests_show_both_spreads_the_mix_and_cash_drag():
    shown, url = run("""
        section.start(); await settle();
        button(node, "Run contribution tests").fire("click"); await settle();
        console.log(JSON.stringify([node.textContent, asked.find((a) => a.includes("/api/plan/contributions"))]));
    """)
    assert "6,000 vs 12-month drip" in shown and "68%" in shown and "+3.00%" in shown
    assert "5-point bands" in shown and "75% ACWI / 25% ISF.L total return in GBP" in shown
    assert "gives up 0.40 points a year" in shown
    assert "second=ISF.L" in url and "lump=" not in url
