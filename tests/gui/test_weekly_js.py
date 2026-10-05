import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE
from tests.gui.test_plan_summary_js import SENT

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

MADE = {"path": "C:/lab/.scratch/research-lab/weekly/page.html", "at": "2026-10-05T08:00:00+00:00",
        "candidates": ["N-02", "N-01"], "rows": 205, "papers": 3, "questions": 1}
CAPS = {"db": {"rules": [{"path": "marks", "read": "view", "write": "interact"}]}, "user": {}}


def run(body: str):
    script = f"""
        {PRELUDE}
        {SENT}
        ANSWERS["GET /api/lab/page"] = {{ last: null, default: "C:/lab/.scratch/research-lab/weekly/page.html",
          capabilities: {json.dumps(CAPS)} }};
        ANSWERS["POST /api/lab/page"] = {json.dumps(MADE)};
        const {{ weeklySection }} = await import({json.dumps((STATIC_DIR / "lib" / "weekly.js").as_uri())});
        const section = weeklySection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8",
                         check=True)
    return json.loads(out.stdout)


def test_the_card_says_where_the_page_will_go_before_the_first_build():
    text = run("""
        await section.start(); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "Not built yet. It will be written to C:/lab/.scratch/research-lab/weekly/page.html." in text
    assert '"path":"marks","read":"view","write":"interact"' in text and "steer.md overrides any mark" in text


def test_build_sends_the_questions_and_shows_the_last_build():
    text, bodies = run("""
        await section.start(); await settle();
        ANSWERS["GET /api/lab/page"] = { ...ANSWERS["GET /api/lab/page"], last: ANSWERS["POST /api/lab/page"] };
        labelled(node, "Open questions").value = "Widen to mid caps?\\n\\n  ";
        button(node, "Build weekly page").fire("click"); await settle(); await settle();
        console.log(JSON.stringify([node.textContent, sent.map((s) => s[1])]));
    """)
    assert bodies == [{"questions": ["Widen to mid caps?"]}]
    assert "Built C:/lab/.scratch/research-lab/weekly/page.html." in text
    assert "Last built 2026-10-05 08:00 UTC: C:/lab/.scratch/research-lab/weekly/page.html (205 rows, candidates " \
           "N-02, N-01, 3 papers, 1 questions)" in text
