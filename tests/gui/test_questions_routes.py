from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.research.llm import ModelError
from oracle.services import discovery_chat_service
from oracle.services.context import open_context
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakeSearcher
from tests.fakes.yahoo import bar
from tests.gui.jobs import run_job, wait_job

QUESTION = "which phone makers gain from summer?"
HEADLINES = ["Samsung Electronics sales jump in heatwave", "Apple phones sell out", "Toyota recalls cars"]


@pytest.fixture
def news():
    n = FakeGoogleNews()
    n.results["summer demand"] = [(f"https://news.test/{i}", t, "Wire", datetime(2026, 9, 22, 20 - i,
                                                                                    tzinfo=timezone.utc))
                                  for i, t in enumerate(HEADLINES)]
    return n


@pytest.fixture
def ctx(tmp_path, yahoo, clock, news):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, news)
    with open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport) as c:
        yield c


@pytest.fixture
def model(monkeypatch):
    m = ScriptedModel()
    m.suggestions = {"queries": ["summer demand"], "local": [], "market_caps": [], "exclude": []}
    monkeypatch.setattr(discovery_chat_service, "configured_model", lambda c: m)
    monkeypatch.setattr(discovery_chat_service, "DdgsSearcher", FakeSearcher)
    return m


@pytest.fixture
async def asking(loaded, yahoo, model):
    for s in ("005930.KS", "AAPL"):
        yahoo.add(s, "USD", "America/New_York", [bar("2026-09-21", 10.0), bar("2026-09-22", 11.0)])
    return loaded


def names(*entries):
    return {"companies": [{"name": n, "why": w, "headlines": list(h)} for n, w, h in entries]}


BOTH = names(("Samsung Electronics", "Sells more in heat", [1]), ("Apple", "Phones sell out", [2]))


async def test_asking_runs_as_a_job_and_the_answer_is_saved_with_its_shortlist(asking, model):
    model.replies = [BOTH]
    job = await run_job(asking, "/api/jobs/ask", question=QUESTION)
    assert (job["state"], job["kind"], job["link"]) == ("done", "ask", "#discover/1")
    result = job["result"]
    assert [(i["symbol"], i["why"], i["new"], i["watched"]) for i in result["shortlist"]] == [
        ("005930.KS", "Sells more in heat", False, False), ("AAPL", "Phones sell out", False, False)]
    [e] = result["shortlist"][0]["evidence"]
    assert (e["title"], e["link"]) == (HEADLINES[0], "https://news.test/0")
    listing = (await asking.get("/api/questions")).json()["questions"]
    assert [(q["id"], q["text"], q["answers"], q["last"]["shortlist"]) for q in listing] == [(1, QUESTION, 1, 2)]
    shown = (await asking.get("/api/questions/1")).json()
    assert shown["asked"] == QUESTION and shown["answers"][0]["id"] == result["id"]


async def test_a_follow_up_and_a_rerun_are_answers_of_their_own_marking_what_is_new(asking, model):
    model.replies = [names(("Samsung Electronics", "Heat", [1])), BOTH, names(("Apple", "Phones", [2]))]
    await run_job(asking, "/api/jobs/ask", question=QUESTION)
    rerun = await run_job(asking, "/api/jobs/ask/1/rerun")
    assert [(i["symbol"], i["new"]) for i in rerun["result"]["shortlist"]] == [("005930.KS", False), ("AAPL", True)]
    follow = await run_job(asking, "/api/jobs/ask", question="only phone makers", refines=1)
    assert follow["link"] == "#discover/2"
    body = follow["result"]
    assert [i["symbol"] for i in body["gone"]] == ["005930.KS"] and body["compared_to"] == rerun["result"]["id"]
    shown = (await asking.get("/api/questions/2")).json()
    assert shown["asked"] == f"{QUESTION} Follow-up: only phone makers"
    assert [q["id"] for q in shown["chain"]] == [1, 2]
    assert [q["id"] for q in (await asking.get("/api/questions/1")).json()["follow_ups"]] == [2]


async def test_add_to_discovered_from_the_shortlist(asking, model):
    model.replies = [BOTH]
    job = await run_job(asking, "/api/jobs/ask", question=QUESTION)
    added = await asking.post(f"/api/questions/answers/{job['result']['id']}/items/1/discovered")
    assert added.status_code == 200 and added.json()["symbol"] == "005930.KS"
    [item] = (await asking.get("/api/discovery")).json()["items"]
    assert item["why"] == f'an answer to your question "{QUESTION}"' and item["evidence"][0]["title"] == HEADLINES[0]
    again = await asking.post(f"/api/questions/answers/{job['result']['id']}/items/1/discovered")
    assert again.status_code == 400 and "already" in again.json()["error"]


async def test_bad_questions_are_refused_before_a_job_starts(asking):
    assert (await asking.post("/api/jobs/ask", params={"question": "  "})).status_code == 400
    assert (await asking.post("/api/jobs/ask", params={"question": "x", "refines": 9})).status_code == 404
    assert (await asking.post("/api/jobs/ask/9/rerun")).status_code == 404
    assert (await asking.get("/api/questions/9")).status_code == 404


async def test_a_failed_answer_can_be_retried_from_activity(asking, model):
    model.replies = [ModelError("ollama is not running"), BOTH]
    failed = await run_job(asking, "/api/jobs/ask", question=QUESTION)
    assert failed["state"] == "failed" and "not running" in failed["error"]
    retried = (await asking.post(f"/api/activity/{failed['id']}/retry")).json()
    done = await wait_job(asking, retried)
    assert done["state"] == "done" and done["subject"] == "1" and len(done["result"]["shortlist"]) == 2
    answers = (await asking.get("/api/questions/1")).json()["answers"]
    assert [a["status"] for a in answers] == ["done", "failed"]


async def test_the_auto_switch_is_saved_and_an_auto_answer_says_what_validation_made_of_each(asking, model):
    model.replies = [BOTH, BOTH]
    model.roles = {"relevance_check": {"answer": "yes", "reason": "Sells phones"}}
    await run_job(asking, "/api/jobs/ask", question=QUESTION)
    switched = await asking.put("/api/questions/1/auto", json={"auto": True})
    assert switched.status_code == 200 and switched.json()["auto"] is True
    assert (await asking.get("/api/questions")).json()["questions"][0]["auto"] is True
    job = await run_job(asking, "/api/jobs/ask/1/rerun")
    first = job["result"]["shortlist"][0]
    assert first["validation"]["added"] is False
    assert first["validation"]["checks"][-1] == {"name": "evidence", "passed": False,
                                                 "detail": "only 1 outlet (wire) named it in the last 90 days; "
                                                           "2 are needed"}
    assert (await asking.put("/api/questions/9/auto", json={"auto": True})).status_code == 404
    assert (await asking.put("/api/questions/1/auto", json={"auto": "yes"})).status_code == 400
