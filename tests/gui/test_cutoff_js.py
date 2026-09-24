import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

STAGES = [{"stage": s, "profile": "default"} for s in ("queries", "research", "notes", "roles")] + [
    {"stage": "verdict", "profile": "big"}]
PROBE = {"provider": "ollama", "model": "qwen3:4b", "profile": "default", "question_set": "2026-09-24.2",
         "claimed": "2025-05-20", "cutoff": "2025-09-30", "last_known": "2025-08", "probed_at": "2026-09-24T10:00:00Z",
         "months": [{"month": "2025-08", "right": 3, "total": 3, "known": True},
                    {"month": "2025-09", "right": 0, "total": 3, "known": False}],
         "answers": [{"event_id": "kirk", "day": "2025-09-10", "question": "Who was shot?", "expected": "Charlie Kirk",
                      "reply": "unknown", "right": False}]}
DEFAULT = {"profile": "default", "provider": "ollama", "model": "qwen3:4b", "claimed": "2025-05-20",
           "cutoff": "2025-09-30", "probe": PROBE}
BIG = {"profile": "big", "provider": "ollama", "model": "big-model", "claimed": "2025-07-01", "cutoff": None,
       "probe": None}
UNPROBED = {"stages": STAGES, "models": [DEFAULT, BIG], "cutoff": None, "set_by": None, "replay_from": None,
            "unprobed": ["big"], "question_set": "2026-09-24.2",
            "note": "replays run on default, big: probe big first (Probe cutoff on the Ledgers tab)."}
PROBED = {**UNPROBED, "models": [DEFAULT, {**BIG, "cutoff": "2025-12-31",
                                           "probe": {**PROBE, "profile": "big", "cutoff": "2025-12-31"}}],
          "cutoff": "2025-12-31", "set_by": "big", "replay_from": "2026-01-01", "unprobed": [], "note": None}


def run(body: str, cutoff: dict = UNPROBED):
    script = f"""
        {PRELUDE}
        ANSWERS = {{
          "GET /api/models": {{ profiles: [{{ name: "default" }}, {{ name: "big" }}] }},
          "GET /api/replay/cutoff": {json.dumps(cutoff)},
          "GET /api/activity": {{ jobs: [], elsewhere: [] }},
          "POST /api/jobs/cutoff-probe": {{ id: 3, kind: "probe", state: "done", result: {{ probes: [] }} }},
        }};
        const {{ cutoffSection }} = await import({json.dumps((STATIC_DIR / "lib" / "cutoff_probe.js").as_uri())});
        const section = cutoffSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


SHOW = "section.start(); await settle(); console.log(JSON.stringify(node.textContent));"


def test_an_unprobed_stage_model_is_named_and_replays_are_refused_until_it_is_probed():
    text = run(SHOW)
    assert "Replays are refused: replays run on default, big: probe big first" in text
    assert "verdict on big" in text and "big: ollama big-model, claimed cutoff 2025-07-01" in text
    assert "Not probed yet." in text and "Measured cutoff" not in text


def test_every_probe_shows_its_cutoff_months_and_replies_and_the_latest_cutoff_wins():
    text = run(SHOW, PROBED)
    assert "Measured cutoff: 2025-12-31 (set by big)." in text and "Replays start after it, from 2026-01-01." in text
    assert "default: ollama qwen3:4b, claimed cutoff 2025-05-20" in text and "Cutoff 2025-09-30" in text
    assert "Month by month" in text and "Questions and replies (1)" in text
    assert "Who was shot?" in text and "Charlie Kirk" in text and "wrong" in text


def test_a_claim_above_the_measured_cutoff_is_shown_as_the_cutoff():
    raised = {**PROBED, "models": [{**DEFAULT, "claimed": "2026-01-15", "cutoff": "2026-01-15"}, PROBED["models"][1]]}
    assert "Cutoff 2026-01-15 (its claimed cutoff; measured 2025-09-30)" in run(SHOW, raised)


def test_a_model_that_knows_too_much_shows_why_no_replay_can_start():
    text = run(SHOW, {**PROBED, "note": "big knew events up to 2026-09: no replay day after that has a month."})
    assert "Replays are refused: big knew events up to 2026-09" in text and "Replays start after it" not in text


def test_probe_sends_the_picked_model_and_rereads_the_cutoff_for_it():
    asked = run("""
        section.start(); await settle();
        labelled(node, "Model").value = "big";
        asked.length = 0;
        button(node, "Probe cutoff").fire("click"); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert asked == ["POST /api/jobs/cutoff-probe?model=big", "GET /api/replay/cutoff?model=big"]


def test_per_slot_sends_no_model_and_a_refusal_shows_under_the_button():
    text = run("""
        ANSWERS["POST /api/jobs/cutoff-probe"] = { status: 503, error: "ollama is not running" };
        section.start(); await settle();
        asked.length = 0;
        button(node, "Probe cutoff").fire("click"); await settle();
        console.log(JSON.stringify([asked[0], node.textContent]));
    """)
    assert text[0] == "POST /api/jobs/cutoff-probe" and "ollama is not running" in text[1]


def test_picking_another_model_shows_its_cutoff():
    asked = run("""
        section.start(); await settle();
        const pick = labelled(node, "Model");
        pick.value = "big"; asked.length = 0;
        pick.fire("change"); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert asked == ["GET /api/replay/cutoff?model=big"]
