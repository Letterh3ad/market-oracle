import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_ledger_research_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def run(body: str):
    script = f"""
        {PRELUDE}
        document.getElementById = () => ({{ hidden: true, textContent: "" }});
        const m = await import({json.dumps((STATIC_DIR / "lib" / "questions.js").as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def item(n, symbol, name, **flags):
    return {"n": n, "name": name, "symbol": symbol, "company": f"{name} AG", "why": f"why {name}", "skipped": None,
            "new": False, "watched": False, "discovered": False,
            "evidence": [{"id": n, "title": f"{name} news", "link": f"https://news.test/{n}", "outlet": "Wire",
                          "published_at": "2026-09-23T08:00:00+00:00"}], **flags}


ANSWER = {
    "id": 7, "question_id": 3, "model": "ollama qwen3", "started_at": "2026-09-24T10:15:00+00:00", "status": "done",
    "stage": None, "error": None, "searches": ["Google News: grid battery Europe"], "notes": ["only mid caps"],
    "compared_to": 5,
    "shortlist": [item(1, "ENR.DE", "Siemens Energy", new=True), item(2, "FLNC", "Fluence", watched=True),
                  item(3, "NIBE-B.ST", "Nibe", discovered=True)],
    "skipped": [{"n": 4, "name": "Tesla", "skipped": "on the never list"}],
    "gone": [{"n": 1, "name": "RWE", "company": "RWE AG", "symbol": "RWE.DE"}],
}


def test_an_answer_lists_the_shortlist_with_why_evidence_and_marks():
    out = run(f"""const card = m.answerCard({json.dumps(ANSWER)});
        console.log(JSON.stringify({{ text: card.textContent,
          links: card.all((n) => n.tag === "a").map((a) => a.attrs.href),
          badges: card.all((n) => n.className?.startsWith("badge")).map((b) => b.textContent) }}));""")
    text = out["text"]
    assert "Answered 2026-09-24 10:15, ollama qwen3. Searched Google News: grid battery Europe." in text
    assert '"new" marks what answer 5 did not have.' in text
    assert "why Siemens Energy" in text and "2026-09-23 Siemens Energy news (Wire)" in text
    assert "Gone since then: RWE AG (RWE.DE)" in text and "Left off (1)" in text and "Tesla: on the never list" in text
    assert "only mid caps" in text
    assert out["links"][:2] == ["#company/ENR.DE", "https://news.test/1"]
    assert out["badges"] == ["new", "watched", "discovered"]


def test_each_row_offers_only_the_buttons_that_still_apply_and_they_call_the_existing_routes():
    out = run(f"""
        let changes = 0;
        const card = m.answerCard({json.dumps(ANSWER)}, () => changes++);
        const buttons = card.all((n) => n.tag === "button");
        const labels = buttons.map((b) => b.textContent);
        for (const b of buttons) {{ b.fire("click"); await settle(); }}
        console.log(JSON.stringify({{ labels, after: buttons.map((b) => b.textContent), asked, changes }}));
    """)
    assert out["labels"] == ["Watch", "Research", "Add to discovered", "Research", "Watch", "Research"]
    assert out["asked"] == ["POST /api/watchlists/main/items", "POST /api/jobs/research?symbol=ENR.DE",
                            "POST /api/questions/answers/7/items/1/discovered", "POST /api/jobs/research?symbol=FLNC",
                            "POST /api/watchlists/main/items", "POST /api/jobs/research?symbol=NIBE-B.ST"]
    assert out["after"][:3] == ["Watched", "Research queued", "Added"]
    assert out["changes"] == 3, "watch and add reload the answer; research only queues"


def test_running_and_failed_answers_say_so():
    out = run("""console.log(JSON.stringify([
        m.answerCard({ status: "running", stage: "reading 2 of 4" }).textContent,
        m.answerCard({ status: "failed", started_at: "2026-09-24T10:15:00+00:00", model: "ollama qwen3",
                       error: "ollama is not running" }).textContent]));""")
    assert out == ["Answering: reading 2 of 4...", "Failed (2026-09-24 10:15, ollama qwen3): ollama is not running"]


def test_the_question_list_links_each_question_and_says_how_it_went():
    listing = {"questions": [
        {"id": 4, "text": "only mid caps", "refines": 3, "answers": 1,
         "last": {"id": 8, "status": "done", "started_at": "2026-09-24T11:00:00+00:00", "shortlist": 2}},
        {"id": 3, "text": "grid batteries?", "refines": None, "answers": 0, "last": None}]}
    out = run(f"""const list = m.questionList({json.dumps(listing)}, 3);
        console.log(JSON.stringify({{ text: list.textContent,
          links: list.all((n) => n.tag === "a").map((a) => a.attrs.href) }}));""")
    assert "only mid caps (follow-up to 3) 1 answer; done 2026-09-24, 2 shortlisted" in out["text"]
    assert "grid batteries? 0 answers; not answered yet" in out["text"]
    assert out["links"] == ["#discover/4"], "the open question is not a link"
    empty = run("""console.log(JSON.stringify(m.questionList({ questions: [] }, null).textContent));""")
    assert empty.startswith("No questions yet.")


def test_a_question_shows_what_it_refines_its_follow_ups_and_older_answers_folded():
    q = {"id": 4, "text": "only mid caps", "asked": "grid batteries? Follow-up: only mid caps",
         "chain": [{"id": 3, "text": "grid batteries?"}, {"id": 4, "text": "only mid caps"}],
         "follow_ups": [{"id": 6, "text": "no utilities"}],
         "answers": [ANSWER, {**ANSWER, "id": 5, "started_at": "2026-09-23T09:00:00+00:00"}]}
    out = run(f"""const view = m.questionView({json.dumps(q)}, () => {{}});
        console.log(JSON.stringify({{ text: view.textContent,
          links: view.all((n) => n.tag === "a").map((a) => a.attrs.href).filter((h) => h.startsWith("#discover")),
          folded: view.all((n) => n.tag === "summary").map((s) => s.textContent) }}));""")
    assert out["text"].startswith("grid batteries? Follow-up: only mid caps")
    assert "Refines: grid batteries?" in out["text"] and "Follow-ups: no utilities" in out["text"]
    assert out["links"] == ["#discover/3", "#discover/6"]
    assert "Answer 5, 2026-09-23: done, 3 shortlisted" in out["folded"]
