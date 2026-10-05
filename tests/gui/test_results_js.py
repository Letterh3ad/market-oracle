"""The Research tab's results table card (research-lab 06), run in node on the real route's answer."""

import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.fakes.results import build
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def run(table: dict, body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/lab/table"] = {json.dumps(table)};
        const opened = [];
        const {{ resultsSection }} = await import({json.dumps((STATIC_DIR / "lib" / "results.js").as_uri())});
        const section = resultsSection({{ openMethod: (r) => opened.push(`method ${{r}}`),
          openTest: (k) => opened.push(`test ${{k}}`), pick: (r) => opened.push(`pick ${{r}}`) }});
        const node = section.node;
        await section.start(); await settle();
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


@pytest.fixture
async def table(ctx, client, monkeypatch):
    build(ctx, monkeypatch)
    return (await client.get("/api/lab/table")).json()


def test_the_card_shows_every_column_and_the_deaths(table):
    text, rows, verdicts = run(table, """
        const rows = node.all((n) => n.tag === "tr" && n.attrs["data-row"] !== undefined);
        console.log(JSON.stringify([node.textContent, rows.length,
          node.all((n) => n.attrs["data-verdict"] !== undefined).map((n) => n.attrs["data-verdict"])]));
    """)
    assert "10 rows. Deaths by stage: literature 1, power 1, build 0, train 0, confirm 2, unrecorded 1." in text
    assert "By verdict: pass 1, fail 4, could-not-see 1, dropped 1, open 3." in text
    assert "LLM headline sentiment" in text and "1. LLM and NLP on text" in text
    assert "replicated" in text and "Jegadeesh and Titman 1993 (replicated)" in text
    assert "GPT scores predict next-day returns." in text and "2026-10-04 go by user: worth a look" in text
    assert "OSAP Mom12m" in text and "stop: OSAP Mom12m: large caps since 2000 +0.30%/month t 1.00" in text
    assert "could not see: expected IC 0.0050 at 20 d" in text
    assert "momentum_12_1" in text and "+0.0123" in text and "+0.0071" in text and "0.45" in text
    assert "turnover 31.0%, break-even 0.42%" in text and "t > 2.00" in text
    assert "+0.40%" in text and "-3.31%" in text and "deflated Sharpe 0.120" in text
    assert "a test no method names" in text and "gone_key: not in the registry" in text
    assert "stopped at confirm" in text and "Result: noise" in text
    assert rows == 12, "one row per test line: X-01 and event:1.01 have two each"
    assert verdicts == ["open", "fail", "could-not-see", "open", "fail", "fail", "dropped", "open", "pass", "fail"]


def test_the_filters_fetch_again_with_their_values(table):
    asked, options = run(table, """
        labelled(node, "Verdict").value = "fail";
        labelled(node, "Verdict").fire("change"); await settle();
        labelled(node, "Stage").value = "power";
        labelled(node, "Family").value = "1. LLM and NLP on text";
        labelled(node, "Family").fire("change"); await settle();
        console.log(JSON.stringify([asked, ["Family", "Stage", "Verdict"].map((l) =>
          labelled(node, l).children.map((o) => o.attrs.value))]));
    """)
    assert asked == ["GET /api/lab/table?stage=funnel", "GET /api/lab/table?stage=funnel&verdict=fail",
                     "GET /api/lab/table?family=1.+LLM+and+NLP+on+text&stage=power&verdict=fail"]
    family, stage, verdict = options
    assert family == ["", *table["families"]] and verdict == ["", *table["verdict_words"]]
    assert stage == ["", "funnel", *table["stages"]]


def test_notes_registration_and_pick_open_the_other_cards(table):
    opened = run(table, """
        button(node, "Notes").fire("click");
        button(node, "Registration").fire("click");
        button(node, "Pick").fire("click");
        console.log(JSON.stringify(opened));
    """)
    assert opened == ["method A-01", "test momentum_12_1", "pick A-01"]


def test_a_failed_load_says_so():
    text = run({"status": 500, "error": "boom"}, "console.log(JSON.stringify(node.textContent));")
    assert "Could not load the results table: boom" in text


def test_power_every_mark_and_a_test_shown_under_an_earlier_method_are_visible(table):
    rows = {r["ref"] or r["tests"][0]["key"]: r for r in table["rows"]}
    rows["X-01"]["tests"][0]["lines"][0]["verdict"] = "FAIL"
    rows["A-01"]["notes"].append({"mark": "dig", "by": "user", "at": "2026-10-03T09:00:00+00:00", "note": ""})
    rows["A-02"]["shared_tests"] = ["momentum_12_1"]
    rows["A-02"]["tested_under"] = [{"key": "momentum_12_1", "ref": "X-01", "stage": "confirm", "verdict": "fail"}]
    text = run(table, "console.log(JSON.stringify(node.textContent));")
    assert "power 0.0158" in text and "0.0158" not in rows["X-01"]["tests"][0]["lines"][0]["verdict"]
    assert "2026-10-03 dig by user" in text and "2026-10-04 go by user: worth a look" in text
    assert "momentum_12_1: tested under X-01 (confirm, fail)" in text
    assert "shown under an earlier method" not in text
