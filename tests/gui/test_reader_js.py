import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

STATUS = {"model": "qwen3:4b-instruct-2507-q8_0",
          "last_run": {"stopped": "cancelled", "finished_at": "2026-09-30T23:00:00Z",
                       "summary": "stopped early (cancelled): 40 of 100 headlines read"}}
PLAN = {"model": "ollama:qwen3:4b-instruct-2507-q8_0", "groups": 3, "pairs": 150, "subject_articles": 100,
        "unscored": 60, "rate": 16.6, "basis": "the bake-off's rate", "estimate_s": 3.6}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/reader"] = {json.dumps(STATUS)};
        ANSWERS["GET /api/reader/plan"] = {json.dumps(PLAN)};
        ANSWERS["GET /api/activity"] = {{ jobs: [] }};
        ANSWERS["POST /api/jobs/reader"] = {{ id: 4, kind: "reader", state: "queued", progress: null }};
        const {{ readerSection }} = await import({json.dumps((STATIC_DIR / "lib" / "reader.js").as_uri())});
        const section = readerSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_names_the_model_and_the_last_run_and_starts_or_estimates_a_run():
    asked, text = run("""
        section.show(); await settle();
        asked.length = 0;
        button(node, "Count headlines and estimate").fire("click"); await settle();
        button(node, "Read history with the stronger reader").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["GET /api/reader/plan?own_only=true", "POST /api/jobs/reader?own_only=true"]
    assert "Model: qwen3:4b-instruct-2507-q8_0" in text
    assert "Last run, 2026-09-30 23:00 UTC: stopped early (cancelled)" in text
    assert "60 of 100 headlines left to read (150 pairs), about 0.0 h at 16.6 a second" in text
    assert "The stronger reader is queued" in text and "Activity tab" in text


def test_unticking_own_news_only_asks_for_every_pair():
    asked = run("""
        section.show(); await settle();
        asked.length = 0;
        labelled(node, "Own news only").checked = false;
        button(node, "Read history with the stronger reader").fire("click"); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert asked == ["POST /api/jobs/reader"]
