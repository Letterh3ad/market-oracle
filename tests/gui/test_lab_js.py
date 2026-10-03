import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

ROW = {"key": "momentum_12_1", "label": "12-1 momentum", "kind": "signal", "registered_on": "2026-10-01",
       "horizon": 20, "measure": "IC", "value": 0.0042, "t": 0.45, "bar": "t > 2.00",
       "verdict": "FAIL (could not see an IC this small: needed 0.0110)", "power": 0.011, "legacy": True,
       "commit_hash": "2abe9c6" + "0" * 33, "run_at": "2026-10-01T12:19:56+00:00", "note": ""}
DRAFT = {**ROW, "key": "new_signal", "label": "A new signal", "registered_on": "2026-10-04", "measure": "",
         "value": None, "t": None, "bar": "t >= 3.02", "verdict": "not committed: no data run yet", "power": None,
         "legacy": False, "commit_hash": None, "run_at": None}
BOARD = {"rows": [ROW, DRAFT], "n": 20, "next_bar": 3.0441}
REG = {"key": "new_signal", "label": "A new signal", "kind": "signal", "registered_on": "2026-10-04", "horizons": [20],
       "rule": "the rule", "universe": "the testbed", "expected_sign": "positive", "expected_size": "IC 0.02",
       "source": "a paper", "power": "0.01", "costs": "none", "bar": 3.0233, "bar_inclusive": True,
       "bar_rule": "confirm IC above 0, t >= 3.02", "kill_stage": "confirm", "parent": None,
       "different_because": None, "commit_hash": None, "legacy": False, "result_key": "new_signal", "note": "",
       "bar_text": "t >= 3.02"}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/lab/scoreboard"] = {json.dumps(BOARD)};
        ANSWERS["GET /api/lab/tests/new_signal"] = {json.dumps(REG)};
        ANSWERS["POST /api/lab/tests"] = {json.dumps(REG)};
        ANSWERS["PUT /api/lab/tests/new_signal/commit"] = {json.dumps({**REG, "commit_hash": "a" * 40})};
        const bodies = [];
        const realFetch = globalThis.fetch;
        globalThis.fetch = async (url, options) => {{ if (options?.body) bodies.push(JSON.parse(options.body));
          return realFetch(url, options); }};
        const {{ scoreboardSection }} = await import({json.dumps((STATIC_DIR / "lib" / "lab.js").as_uri())});
        const section = scoreboardSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_scoreboard_shows_each_test_its_bar_verdict_and_power():
    text, verdicts = run("""
        await section.start(); await settle();
        const v = node.all((n) => n.attrs?.["data-verdict"] != null).map((n) => n.className);
        console.log(JSON.stringify([node.textContent, v]));
    """)
    assert "20 hypotheses registered" in text and "t >= 3.04" in text
    assert "12-1 momentum" in text and "+0.0042" in text and "0.45" in text and "t > 2.00" in text
    assert "could not see" in text and "0.0110" in text and "before the lab" in text
    assert "not committed: no data run yet" in text
    assert verdicts == ["small down", "small dim"]


def test_a_registration_opens_and_its_commit_is_recorded():
    asked_text, sent = run("""
        await section.start(); await settle();
        const row = node.all((n) => n.attrs?.["data-test"] === "new_signal")[0];
        button(row, "Registration").fire("click"); await settle();
        labelled(node, "Registration commit").value = "abcdef1";
        button(node, "Record commit").fire("click"); await settle();
        console.log(JSON.stringify([asked.join("|") + node.textContent, bodies]));
    """)
    assert "GET /api/lab/tests/new_signal" in asked_text and "PUT /api/lab/tests/new_signal/commit" in asked_text
    assert "the rule" in asked_text and "Pass rule" in asked_text and "committed: its data runs may start" in asked_text
    assert sent == [{"hash": "abcdef1"}]


def test_the_form_registers_a_test_from_the_template():
    text, sent = run("""
        await section.start(); await settle();
        labelled(node, "Key (lower case, e.g. sue_v2)").value = "new_signal";
        labelled(node, "Exact rule").value = "the rule";
        labelled(node, "Horizons (trading days, comma separated)").value = "20, 60";
        button(node, "Register").fire("click"); await settle();
        console.log(JSON.stringify([node.textContent, bodies]));
    """)
    assert "Registered new_signal" in text and "Commit it to DECISIONS" in text
    assert sent[0]["key"] == "new_signal" and sent[0]["horizons"] == [20, 60] and sent[0]["kind"] == "signal"
    assert sent[0]["parent"] is None and sent[0]["expected_sign"] == "positive"


def test_the_research_tab_renders_its_section_list():
    text = run(f"""
        const tab = (await import({json.dumps((STATIC_DIR / "tabs" / "research.js").as_uri())})).default;
        const pane = document.createElement("div");
        await tab.render(pane); await settle();
        console.log(JSON.stringify([tab.id, tab.label, pane.textContent]));
    """)
    assert text[:2] == ["research", "Research"] and "Scoreboard" in text[2] and "12-1 momentum" in text[2]
    assert text[2].index("Scoreboard") < text[2].index("Literature index")
