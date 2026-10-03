import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE
from tests.gui.test_plan_summary_js import SENT

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

PENDING = {"id": 2, "changes": {"monthly": [500, 50]}, "made_at": "2026-10-03T12:00:00+00:00",
           "due": "2026-10-10T12:00:00+00:00", "state": "pending", "settled_at": None, "note": ""}
GUARD = {"rule": "Never sell in a fall.", "pending": PENDING, "cool_off_days": 7,
         "history": [{**PENDING, "id": 1, "changes": {"rule": ["", "x"]}, "state": "cancelled",
                      "settled_at": "2026-10-02T09:00:00+00:00"}]}
FALL = {"peak_day": "2020-02-19", "peak": 1.0, "trough_day": "2020-03-23", "trough": 0.75, "fall": -0.25,
        "recovered": "2020-08-18"}
PAYING = {"value_at_trough": 15400, "paid_at_trough": 20500, "worst_gap": -5100, "worst_day": "2020-03-23",
          "even": "2020-06-30", "paid_at_even": 22000}
ROW = {"crash": {"name": "2020", "label": "Covid crash", "start": "2020-01-01", "end": "2020-12-31"},
       "fund": "all_world", "fund_label": "All-world", "stand_in": False, "fall": FALL, "months_down": 2,
       "months_back": 5, "pot_low": 15000, "pot_loss": -5000, "paying": PAYING, "months_even": 5, "note": ""}
NONE = {**ROW, "crash": {**ROW["crash"], "name": "2000-02"}, "fall": None, "fund": "", "note": "No stored series spans it."}
DRAW = {"fund": "all_world", "fund_label": "All-world", "source": "proxy: ACWI", "kind": "total return",
        "pot": 20000, "example": False, "monthly": 500, "rows": [NONE, ROW], "notes": ["In GBP, total return."]}


def run(body: str):
    script = f"""
        {PRELUDE}
        {SENT}
        ANSWERS["GET /api/plan/guardrails"] = {json.dumps(GUARD)};
        ANSWERS["GET /api/plan/drawdowns"] = {json.dumps(DRAW)};
        ANSWERS["PUT /api/plan/rule"] = {{ rule: "Never sell in a fall.", applied: [], pending: {json.dumps(PENDING)} }};
        ANSWERS["DELETE /api/plan/pending"] = {{ ...{json.dumps(PENDING)}, state: "cancelled" }};
        const {{ guardrailsSection }} = await import({json.dumps((STATIC_DIR / "lib" / "guardrails.js").as_uri())});
        let changes = 0;
        const section = guardrailsSection({{ changed: () => changes++ }});
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_the_card_shows_the_rule_the_pending_change_and_each_crash():
    text, rule = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent, labelled(node, "No-sell rule").value]));
    """)
    assert rule == "Never sell in a fall." and "Never sell in a fall." in text
    assert "Takes effect 2026-10-10 12:00 UTC" in text and "monthly: 500 → 50" in text
    assert "cancelled 2026-10-02 09:00" in text
    assert "No stored series spans it." in text and "Covid crash" in text and "-25.0%" in text
    assert "£15,000" in text and "-£5,000" in text and "2020-08-18 (5 mo after the low)" in text
    assert "even 2020-06-30" in text and "In GBP, total return." in text


def test_save_rule_cancel_and_show_send_their_requests():
    sent, asked, note, changes = run("""
        section.start(); await settle();
        labelled(node, "No-sell rule").value = "Hold through it.";
        button(node, "Save rule").fire("click"); await settle();
        const note = node.all((n) => (n.className ?? "").startsWith("form-note")).map((n) => n.textContent).join("|");
        button(node, "Cancel pending change").fire("click"); await settle();
        labelled(node, "Pot (GBP)").value = "20000";
        button(node, "Show").fire("click"); await settle();
        console.log(JSON.stringify([sent, asked, note, changes]));
    """)
    assert sent == [["/api/plan/rule", {"rule": "Hold through it."}], ["/api/plan/pending", {}]]
    assert "DELETE /api/plan/pending" in asked and "GET /api/plan/drawdowns?pot=20000" in asked
    assert "Change pending until 2026-10-10 12:00 UTC" in note
    assert changes == 2
