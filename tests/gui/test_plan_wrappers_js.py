import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE
from tests.gui.test_plan_summary_js import SENT

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

SETTINGS = {"lisa": False, "salary": 0, "employee_pct": 0, "employer_pct": 0, "on_salary": False, "sacrifice": False,
            "born": 0, "needed_before": -1, "retire_tax": 20, "pension_fee": 0.3, "pension_charge": 1.8}
EMPTY = {"settings": SETTINGS, "lisa": False, "monthly": 0, "growth": 0.05, "age": None, "access_age": None,
         "years": None, "paid_years": None, "workplace": None, "rows": [], "steps": [], "isa_yearly": 0,
         "isa_allowance": 20000, "missing": [{"step": "salary", "text": "Enter your salary."},
                                             {"step": "needed", "text": "Say how much you may need."}],
         "notes": [], "tax_year": "2026-27", "checked": "2026-10-03",
         "sources": [["ISA allowance", "https://www.gov.uk/individual-savings-accounts"]]}


def row(key, label, at_60=None):
    return {"key": key, "label": label, "paid_in": 24000, "added": 6000, "fees": 2000, "pot": 66000,
            "spendable": 56000, "at_60": at_60, "access": "from 57", "per_pound": 2.7}


FILLED = {**EMPTY, "settings": {**SETTINGS, "lisa": True, "salary": 30000, "employee_pct": 5, "employer_pct": 3,
                                "born": 1996, "needed_before": 200},
          "lisa": True, "monthly": 600, "age": 30, "access_age": 57, "years": 27, "paid_years": 20,
          "rows": [row("isa", "Stocks and Shares ISA", 62000), row("pension", "Workplace pension", 64000),
                   row("lisa", "Lifetime ISA", 78000)],
          "steps": [{"wrapper": "pension", "monthly": 79, "into": 158, "text": "Workplace pension first: employer."},
                    {"wrapper": "isa", "monthly": 200, "into": 200, "text": "Stocks and Shares ISA: £200 a month."}],
          "isa_yearly": 6400, "missing": [], "notes": ["Growth is assumed."]}


def run(body: str, answer=EMPTY):
    script = f"""
        {PRELUDE}
        {SENT}
        ANSWERS["GET /api/plan/wrappers"] = {json.dumps(answer)};
        ANSWERS["PUT /api/plan/wrappers"] = {{}};
        const {{ wrappersSection }} = await import({json.dumps((STATIC_DIR / "lib" / "plan_wrappers.js").as_uri())});
        const section = wrappersSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8",
                         check=True)
    return json.loads(out.stdout)


def test_nothing_entered_shows_blank_fields_the_missing_list_and_lisa_off():
    text, salary, needed, retire, lisa = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent, labelled(node, "Salary (GBP a year)").value,
          labelled(node, "Needed before pension age (GBP a month)").value, labelled(node, "Tax when drawn (%)").value,
          labelled(node, "Show the Lifetime ISA").checked]))
    """)
    assert "Missing" in text and "Enter your salary." in text and "Rates 2026-27" in text
    assert (salary, needed, retire, lisa) == ("", "", "20", False)


def test_the_comparison_shows_the_order_rows_with_at_60_and_allowance_use():
    text, wrappers = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent,
          node.all((n) => n.attrs?.["data-wrapper"]).map((n) => n.attrs["data-wrapper"])]));
    """, FILLED)
    assert "Workplace pension first" in text and "At 60" in text and "Per £1" in text and "£2.70" in text
    assert "for 20 years" in text and "£6,400 a year of the £20,000 allowance" in text
    assert wrappers == ["isa", "pension", "lisa"]


def test_the_lisa_switch_saves_at_once_and_save_sends_the_numbers():
    sent = run("""
        section.start(); await settle();
        const box = labelled(node, "Show the Lifetime ISA");
        box.checked = true; box.fire("change"); await settle();
        labelled(node, "Salary (GBP a year)").value = "30000";
        labelled(node, "Year of birth").value = "1996";
        labelled(node, "Salary sacrifice").checked = true;
        button(node, "Save").fire("click"); await settle();
        console.log(JSON.stringify(sent));
    """)
    assert sent[0] == ["/api/plan/wrappers", {"lisa": True}]
    url, values = sent[1]
    assert url == "/api/plan/wrappers" and values["salary"] == 30000 and values["born"] == 1996
    assert values["needed_before"] == -1 and values["sacrifice"] is True and values["on_salary"] is False
    assert values["retire_tax"] == 20


def test_compare_keeps_unsaved_edits_and_a_bad_number_is_not_sent():
    kept, sent, text = run("""
        section.start(); await settle();
        labelled(node, "Salary (GBP a year)").value = "41000";
        button(node, "Compare").fire("click"); await settle();
        const kept = labelled(node, "Salary (GBP a year)").value;
        labelled(node, "Employer %").value = "abc";
        button(node, "Save").fire("click"); await settle();
        console.log(JSON.stringify([kept, sent, node.textContent]));
    """)
    assert kept == "41000" and sent == []
    assert "Employer % must be a number" in text and "Saved." not in text
