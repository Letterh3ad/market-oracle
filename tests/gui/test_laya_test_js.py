import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

SEGMENTS = [{"start": "2022-09-21", "end": "2023-03-20", "ic": None, "t": None, "days": 0, "counted": False,
             "why": "no trailing fit", "weights": {"own_mean": 0.0}},
            {"start": "2023-03-21", "end": "2023-09-20", "ic": 0.031, "t": 2.4, "days": 120, "counted": True,
             "why": None, "weights": {"own_mean": 0.7, "own_relevance": -0.3}}]
HORIZON = {"horizon": 5, "lookback": 7, "ic": 0.031, "t": 2.4, "days": 120, "observations": 5000, "segments": SEGMENTS,
           "features": [{"name": "own_mean", "ic": 0.02, "t": 1.1, "days": 120, "positive": 1, "in_score": True},
                        {"name": "own_sum", "ic": 0.01, "t": 0.5, "days": 110, "positive": 1, "in_score": False}],
           "verdict": "INCONCLUSIVE", "passed": False, "positive": 1, "counted": 1, "baseline_ic": 0.04,
           "baseline_t": 2.1, "beats_baseline": False, "momentum_ic": 0.01, "momentum_t": 0.4,
           "beats_momentum": True}
TEST = {"run": "main", "label": "main (links first known by the article's day)", "model": "laya@x",
        "start": "2022-09-21", "end": "2023-09-20", "companies": 51, "articles": {"own": 900, "competitor": 4000},
        "horizons": [HORIZON], "verdict": "INCONCLUSIVE", "passed": False, "run_at": "2026-09-30T23:00:00+00:00",
        "t_method": "Newey-West", "caveat": "Survivorship: today's main list", "id": 1}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/laya/tests"] = {json.dumps({"tests": [TEST], "last_scoring": {"summary": "finished: 51 groups"}})};
        ANSWERS["GET /api/laya/plan"] = {json.dumps({"pairs": 148009, "targets": 51, "unscored": 100, "ms_per_pair": 12.5,
                                                     "estimate_s": 7200, "basis": "measured"})};
        ANSWERS["POST /api/jobs/laya-test"] = {{ id: 4, kind: "laya_test", state: "done",
            result: {{ label: "sensitivity", verdict: "FAIL" }} }};
        const {{ layaTestSection }} = await import({json.dumps((STATIC_DIR / "lib" / "laya_test.js").as_uri())});
        const section = layaTestSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_the_kept_table_verdict_segments_left_out_and_the_caveat():
    [text] = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent]));
    """)
    assert "main (links first known by the article's day): INCONCLUSIVE" in text
    assert "no: no trailing fit" in text and "own_mean +0.70, own_relevance -0.30" in text
    assert "own_sum (secondary)" in text and "Last scoring run: finished: 51 groups." in text
    assert "Survivorship" in text
    assert "News-attention baseline (own_attention, sign fitted): IC +0.0400, t +2.10; the reading does not beat it" in text
    assert "Momentum baseline (own_momentum, sign fitted): IC +0.0100, t +0.40; the reading beats it" in text
    assert "useful only if it beats both baselines" in text


def test_the_card_shows_the_post_cutoff_verdict_with_the_full_period_and_a_standing_fail_beside():
    text, red = run("""
        Object.assign(ANSWERS["GET /api/laya/tests"].tests[0], {
          cutoff: "2025-05-31", full_verdict: "FAIL", post_read: { needed: 40, read: 30 },
          full_horizons: [{ ...ANSWERS["GET /api/laya/tests"].tests[0].horizons[0], verdict: "FAIL", ic: -0.01 }],
          standing: "Laya's FAIL stands: judged on the full period" });
        section.start(); await settle();
        const red = node.all((n) => n.tag === "p" && n.className.includes("down")).map((n) => n.textContent);
        console.log(JSON.stringify([node.textContent, red]));
    """)
    assert "INCONCLUSIVE (verdict on days after the model's training cutoff, 2025-05-31)" in text
    assert ("Full period, days inside the model's training included (not the verdict): FAIL; "
            "5-day IC -0.0100, t +2.40, FAIL.") in text
    assert "Laya's FAIL stands: judged on the full period." in red
    assert "Own headlines after the cutoff read by this model: 30 of 40." in red


def test_a_result_kept_before_the_rule_says_so():
    [text] = run("""
        section.start(); await settle();
        console.log(JSON.stringify([node.textContent]));
    """)
    assert "INCONCLUSIVE (full period: kept before the training-cutoff rule)" in text


def test_scoring_asks_for_the_cpu_only_when_the_box_is_ticked():
    [asked] = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Score history with Laya").fire("click"); await settle();
        labelled(node, "Allow the CPU").checked = true;
        button(node, "Score history with Laya").fire("click"); await settle();
        console.log(JSON.stringify([asked.filter((a) => a.startsWith("POST"))]));
    """)
    assert asked == ["POST /api/jobs/laya-history", "POST /api/jobs/laya-history?allow_cpu=true"]


def test_the_sensitivity_button_starts_that_run_and_the_estimate_reads_the_plan():
    asked, text = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Run sensitivity (links as known today)").fire("click"); await settle();
        button(node, "Count pairs and estimate").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked[0] == "POST /api/jobs/laya-test?sensitivity=true" and "GET /api/laya/plan" in asked
    assert "sensitivity: FAIL" in text and "about 2.0 h at 12.5 ms a pair" in text


def test_a_result_shows_how_many_own_pairs_its_model_scored():
    text, red = run("""
        ANSWERS["GET /api/laya/tests"].tests[0].own_coverage = { needed: 10, scored: 8 };
        section.start(); await settle();
        const red = node.all((n) => n.tag === "p" && n.className.includes("down")).map((n) => n.textContent);
        console.log(JSON.stringify([node.textContent, red]));
    """)
    line = "Own pairs scored by this model: 8 of 10; 2 not scored, so it is not like for like"
    assert line in text and any(r.startswith(line) for r in red)


def test_the_test_runs_on_the_picked_models_scores_and_own_news_only():
    asked, options = run("""
        ANSWERS["GET /api/laya/tests"].models = ["laya-multilingual@e4e9ddf", "ollama:qwen2.5:32b"];
        section.start(); await settle();
        const pick = labelled(node, "Test model");
        const options = pick.children.map((o) => o.textContent);
        pick.value = "ollama:qwen2.5:32b";
        labelled(node, "Own news only").checked = true;
        asked.length = 0;
        button(node, "Run the test").fire("click"); await settle();
        console.log(JSON.stringify([asked.filter((a) => a.startsWith("POST")), options]));
    """)
    assert options == ["laya-multilingual@e4e9ddf", "ollama:qwen2.5:32b"]
    assert asked == ["POST /api/jobs/laya-test?own_only=true&model=ollama%3Aqwen2.5%3A32b"]
