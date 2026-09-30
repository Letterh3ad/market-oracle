import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_research_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

SCORE = {"article_id": 1, "title": "Apple beats estimates", "link": "https://news.test/1", "outlet": "Wire",
         "known_at": "2026-09-22T00:00:00+00:00", "dated": True, "relevant": 0.83, "direction": "up",
         "direction_probs": {"up": 0.61, "down": 0.2, "none": 0.19}, "event": "analyst_rating", "event_prob": 0.4}
UNDATED = {**SCORE, "article_id": 2, "title": "Bad link", "link": "javascript:alert(1)", "outlet": None,
           "known_at": "2026-09-23T00:00:00Z", "dated": False}


def run(body: str):
    script = f"""
        {PRELUDE}
        globalThis.window = {{ scrollX: 0, scrollY: 0, scrollTo: () => {{}} }};
        const m = await import({json.dumps((STATIC_DIR / "lib" / "laya.js").as_uri())});
        const wrap = (nodes) => {{ const box = document.createElement("div"); box.append(...nodes); return box; }};
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_each_score_shows_its_day_probabilities_and_the_uncalibrated_badge():
    body = {"model": "laya-multilingual@e4e9ddf", "enabled": True, "scores": [UNDATED, SCORE]}
    out = run(f"""
        const box = wrap(m.layaTable("AAPL", {json.dumps(body)}, () => {{}}));
        const links = box.all((n) => n.tag === "a").map((a) => a.attrs.href);
        console.log(JSON.stringify({{ text: box.textContent, links }}));
    """)
    text = out["text"]
    assert "uncalibrated" in text and "laya-multilingual@e4e9ddf" in text and "Score with Laya" in text
    assert "2026-09-23 (fetched)Bad link" in text and "2026-09-22Apple beats estimates (Wire)83%up 61%analyst rating 40%" in text
    assert out["links"] == ["https://news.test/1"], "only a web address becomes a link"


def test_the_relevance_column_is_marked_unreliable():
    body = {"model": "x", "enabled": True, "scores": [SCORE]}
    out = run(f"""
        const box = wrap(m.layaTable("AAPL", {json.dumps(body)}, () => {{}}));
        const heads = box.all((n) => n.tag === "th").map((th) => ({{ text: th.textContent, title: th.attrs.title }}));
        const cells = box.all((n) => n.tag === "td").map((td) => td.className ?? null);
        console.log(JSON.stringify({{ heads, cells }}));
    """)
    about = next(h for h in out["heads"] if h["text"].startswith("About it"))
    assert about["text"] == "About it (unreliable)" and "always" in about["title"]
    assert out["cells"][2] == "dim"


def test_score_with_laya_runs_the_job_then_reloads():
    ANSWERS = {"/api/jobs/laya": {"id": 5, "state": "done", "result": {"scored": 1, "stopped": False}}}
    out = run(f"""
        ANSWERS = {json.dumps(ANSWERS)};
        let changes = 0;
        const box = wrap(m.layaTable("AAPL", {{ model: "x", enabled: true, scores: [] }}, () => changes++));
        button(box, "Score with Laya").fire("click");
        await settle(10);
        console.log(JSON.stringify({{ asked, changes, text: box.textContent }}));
    """)
    assert out["asked"] == ["POST /api/jobs/laya?symbol=AAPL"] and out["changes"] == 1
    assert "No headlines scored yet" in out["text"]


def test_laya_off_offers_no_button_and_says_where_to_turn_it_on():
    out = run('console.log(JSON.stringify(wrap(m.layaTable("AAPL", { model: "x", enabled: false, scores: [] }, '
              '() => {})).textContent));')
    assert "Laya is off (Settings, [laya] enabled)." in out and "Score with Laya" not in out
