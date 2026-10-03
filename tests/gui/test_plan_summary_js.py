import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def line(name, label, value, reason, known=True):
    return {"name": name, "label": label, "value": value, "reason": reason, "known": known}


SUMMARY = {
    "lines": [line("fund", "Fund", "All-world: Vanguard FTSE All-World UCITS ETF Acc (VWRP)", "The widest spread."),
              line("wrapper", "Wrapper", "Stocks and Shares ISA", "Tax-free."),
              line("platform", "Platform", "Percentage fee, not entered yet", "Fees matter.", False),
              line("monthly", "Monthly", "not set", "Each month.", False),
              line("overlay", "Trend overlay", "No", "The pre-registered test failed.")],
    "missing": [{"step": "platform", "text": "Enter your platform's prices."},
                {"step": "monthly", "text": "Set your monthly amount (Your plan card)."}],
    "fund": "all_world", "picked": False, "default_fund": "all_world", "monthly": 0, "overlay": False,
    "funds": [["all_world", "All-world"], ["developed", "Developed world"], ["sp500", "S&P 500"]]}

# Records each request's body, so a test can read what Save sent.
SENT = """
const sent = [];
const plainFetch = globalThis.fetch;
globalThis.fetch = async (url, options) => { if (options?.body) sent.push([url, JSON.parse(options.body)]);
  return plainFetch(url, options); };
"""


def run(body: str, summary=SUMMARY):
    script = f"""
        {PRELUDE}
        {SENT}
        ANSWERS["GET /api/plan/summary"] = {json.dumps(summary)};
        ANSWERS["PUT /api/plan/choice"] = {{}};
        const {{ planSummarySection }} = await import({json.dumps((STATIC_DIR / "lib" / "plan_summary.js").as_uri())});
        const section = planSummarySection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_each_line_with_its_reason_and_what_is_missing():
    text, unknown, fund, options = run("""
        section.start(); await settle();
        const warn = node.all((n) => n.className === "warn-text").map((n) => n.textContent);
        const select = labelled(node, "Fund");
        console.log(JSON.stringify([node.textContent, warn, select.value, select.children.map((o) => o.textContent)]));
    """)
    assert "Stocks and Shares ISA" in text and "Tax-free." in text and "VWRP" in text
    assert "Trend overlay" in text and "The pre-registered test failed." in text
    assert "Missing" in text and "Set your monthly amount" in text
    assert unknown == ["Percentage fee, not entered yet", "not set"]
    assert fund == "" and options == ["Default (All-world)", "All-world", "Developed world", "S&P 500"]


def test_save_sends_the_pick_and_monthly_then_reloads():
    sent, asked = run("""
        section.start(); await settle();
        labelled(node, "Fund").value = "sp500";
        labelled(node, "Monthly (GBP)").value = "450";
        button(node, "Save").fire("click"); await settle();
        labelled(node, "Fund").value = "";
        button(node, "Save").fire("click"); await settle();
        console.log(JSON.stringify([sent, asked.filter((a) => a === "GET /api/plan/summary").length]));
    """)
    assert sent == [["/api/plan/choice", {"fund": "sp500", "monthly": 450}],
                    ["/api/plan/choice", {"fund": "default", "monthly": 0}]]
    assert asked == 3


def test_a_saved_pick_is_selected_and_nothing_missing_shows_no_list():
    fund, monthly, text = run("""
        section.start(); await settle();
        console.log(JSON.stringify([labelled(node, "Fund").value, labelled(node, "Monthly (GBP)").value, node.textContent]));
    """, {**SUMMARY, "fund": "sp500", "picked": True, "monthly": 500, "missing": []})
    assert fund == "sp500" and monthly == "500" and "Missing" not in text


def test_a_failed_load_says_so():
    text = run("""
        ANSWERS["GET /api/plan/summary"] = { status: 500, error: "database is locked" };
        section.start(); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "Could not load your plan: database is locked" in text


def test_the_plan_tab_hosts_the_summary_and_the_three_cards_and_ledgers_does_not():
    out = subprocess.run([NODE, "--input-type=module", "-e", f"""
        {PRELUDE}
        ANSWERS["GET /api/plan/summary"] = {json.dumps(SUMMARY)};
        ANSWERS["GET /api/plan/funds"] = {{ result: null }};
        ANSWERS["GET /api/plan/trend"] = {{ result: null }};
        ANSWERS["GET /api/settings"] = {{ settings: [{{ section: "plan", key: "monthly", value: 300 }},
          {{ section: "plan", key: "fund", value: "sp500" }}] }};
        ANSWERS["GET /api/plan/guardrails"] = {{ rule: "", pending: null, history: [], cool_off_days: 7 }};
        ANSWERS["GET /api/plan/drawdowns"] = {{ status: 404, error: "run Compare funds first" }};
        ANSWERS["POST /api/jobs/plan-funds"] = {{ id: 3, kind: "plan_funds", state: "done" }};
        ANSWERS["GET /api/jobs/3"] = {{ id: 3, kind: "plan_funds", state: "done" }};
        const tab = (await import({json.dumps((STATIC_DIR / "tabs" / "plan.js").as_uri())})).default;
        const pane = document.createElement("div");
        await tab.render(pane); await settle();
        const titles = pane.all((n) => n.tag === "h2").map((n) => n.textContent);
        const before = asked.filter((a) => a === "GET /api/plan/summary").length;
        button(pane, "Compare funds").fire("click"); await settle(20);
        const after = asked.filter((a) => a === "GET /api/plan/summary").length;
        const named = (n) => pane.all((x) => x.attrs?.name === n);
        console.log(JSON.stringify([tab.id, titles, before, after, named("monthly").map((x) => x.value),
          named("fund").map((x) => x.value)]));
    """], capture_output=True, text=True, check=True)
    tab, titles, before, after, monthly, fund = json.loads(out.stdout)
    assert tab == "plan"
    assert titles == ["Your plan", "Fund comparison", "Fees and contributions", "Trend overlay test", "Guardrails"]
    assert before == 1 and after == 2
    assert monthly == ["", "300", "300"] and fund[1:] == ["sp500"]
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    ledgers = (STATIC_DIR / "tabs" / "ledgers.js").read_text(encoding="utf-8")
    assert "./tabs/plan.js" in app
    assert not any(name in ledgers for name in ("fundsSection", "trendSection", "planMoneySection"))
