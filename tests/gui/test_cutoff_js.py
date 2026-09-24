import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

PROBE = {"provider": "ollama", "model": "qwen3:4b", "profile": "default", "question_set": "2026-09-24.1",
         "claimed": "2025-05-20", "cutoff": "2025-09-30", "last_known": "2025-08", "probed_at": "2026-09-24T10:00:00Z",
         "replay_from": "2025-10-01", "note": None,
         "months": [{"month": "2025-08", "right": 2, "total": 2, "known": True},
                    {"month": "2025-09", "right": 0, "total": 3, "known": False}],
         "answers": [{"event_id": "kirk", "day": "2025-09-10", "question": "Who was shot?", "expected": "Charlie Kirk",
                      "reply": "unknown", "right": False}]}
EMPTY = {"profile": "default", "provider": "ollama", "model": "qwen3:4b", "claimed": "2025-05-20",
         "question_set": "2026-09-24.1", "probe": None}


def run(body: str, cutoff: dict = EMPTY):
    script = f"""
        {PRELUDE}
        ANSWERS = {{
          "GET /api/models": {{ profiles: [{{ name: "default" }}, {{ name: "big" }}] }},
          "GET /api/replay/cutoff": {json.dumps(cutoff)},
          "GET /api/activity": {{ jobs: [], elsewhere: [] }},
          "POST /api/jobs/cutoff-probe": {{ id: 3, kind: "probe", state: "done", result: {{}} }},
        }};
        const {{ cutoffSection }} = await import({json.dumps((STATIC_DIR / "lib" / "cutoff_probe.js").as_uri())});
        const section = cutoffSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_an_unprobed_model_says_so_and_names_the_claimed_cutoff():
    text = run("section.start(); await settle(); console.log(JSON.stringify(node.textContent));")
    assert "ollama qwen3:4b (profile default) has not been probed yet" in text and "2025-05-20" in text


def test_a_stored_probe_shows_the_cutoff_the_months_and_every_reply():
    text = run("section.start(); await settle(); console.log(JSON.stringify(node.textContent));", {**EMPTY, "probe": PROBE})
    assert "Measured cutoff: 2025-09-30 for ollama qwen3:4b (profile default), probed 2026-09-24" in text
    assert "Replays start after it, from 2025-10-01." in text
    assert "Month by month" in text and "2025-08" in text and "Questions and replies (1)" in text
    assert "Who was shot?" in text and "Charlie Kirk" in text and "wrong" in text


def test_a_model_that_knows_too_much_shows_why_no_replay_can_start():
    probe = {**PROBE, "cutoff": "2026-10-31", "note": "no replay day after that has a month of prices"}
    text = run("section.start(); await settle(); console.log(JSON.stringify(node.textContent));", {**EMPTY, "probe": probe})
    assert "no replay day after that" in text and "Replays start after it" not in text


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
