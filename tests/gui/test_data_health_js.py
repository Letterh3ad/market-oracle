import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def flag(check, symbol, day, detail, explanation=None, db="research"):
    return {"check": check, "db": db, "symbol": symbol, "day": day, "detail": detail, "explanation": explanation}


RUN = {"run_at": "2026-10-02T20:00:00+00:00", "seconds": 35.2, "explained": 1, "total": 3,
       "summary": "3 flags", "notes": ["no explanations: profile big is claude_api"],
       "checks": [
           {"code": "split_jump", "label": "Split-sized jump with no recorded split", "count": 2,
            "rows": [flag("split_jump", "CPWR", "2010-04-15", "close 5.767 to 1.153 (x0.200)", "Likely a bad bar."),
                     flag("split_jump", "MELI.BA", "2024-01-24", "close 37650 to 19280", db="live")]},
           {"code": "bad_price", "label": "Zero or negative price", "count": 0, "rows": []},
           {"code": "member_days", "label": "Testbed member-days missing by year", "count": 1,
            "rows": [flag("member_days", "", "2010", "39417 of 125537 member-days have no bar")]}]}


def run(body: str, answer=None):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/data/checks"] = {json.dumps(answer or {"run": RUN, "nightly": True, "explain": False})};
        ANSWERS["POST /api/jobs/data-check"] = {{ id: 31, kind: "data_check", state: "done", result: {json.dumps(RUN)} }};
        const {{ dataHealthSection }} = await import({json.dumps((STATIC_DIR / "lib" / "data_health.js").as_uri())});
        const section = dataHealthSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_counts_per_check_and_the_flagged_rows_on_drill_down():
    text, rows = run("""
        section.start(); await settle();
        const drills = node.all((n) => n.tag === "details");
        console.log(JSON.stringify([node.textContent, drills.map((d) => d.textContent)]));
    """)
    assert "Split-sized jump with no recorded split" in text and "Zero or negative price" in text
    assert "35.2 s" in text and "3 flags" in text and "claude_api" in text
    assert "Explanations are off" in text and "nightly" in text.lower()
    assert len(rows) == 2, "a check with no flag has nothing to drill into"
    assert "CPWR" in rows[0] and "2010-04-15" in rows[0] and "Likely a bad bar." in rows[0] and "live" in rows[0]
    assert "2010" in rows[1] and "39417" in rows[1]


def test_before_any_run_it_says_so():
    text = run("""
        section.start(); await settle();
        console.log(JSON.stringify(node.textContent));
    """, {"run": None, "nightly": False, "explain": True})
    assert "No data check has run yet" in text and "Explanations are on" in text


def test_run_starts_the_job_and_reloads_when_it_finishes():
    asked = run("""
        section.start(); await settle();
        button(node, "Run data checks").fire("click"); await settle(); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert "POST /api/jobs/data-check" in asked and asked.count("GET /api/data/checks") == 2


def test_a_running_check_found_on_the_activity_list_is_followed():
    out = run("""
        section.start(); await settle();
        section.follow([{ id: 31, kind: "data_check", state: "running", progress: "live database" }]);
        console.log(JSON.stringify(node.textContent));
    """)
    assert "running: live database" in out
