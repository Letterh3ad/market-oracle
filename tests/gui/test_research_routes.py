import asyncio
import threading
from datetime import datetime

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.universe_service import refresh_universe
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import step
from tests.fakes.ollama import FakeOllama
from tests.fakes.web import FakeWeb


@pytest.fixture
def ollama():
    return FakeOllama()


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def ctx(tmp_path, yahoo, clock, ollama, news):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, ollama, news)
    with open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport) as c:
        yield c


@pytest.fixture
async def ready(ctx, client, ollama, news):
    await refresh_universe(ctx)
    news.results["Apple"] = [("https://reut.rs/1", "Apple beats estimates", "Reuters",
                              datetime.fromisoformat("2026-09-22T12:00:00+00:00"))]
    ollama.contents = [step("news_search", "Apple", thought="check the news"), step("final", "Beat [a1].")]
    ollama.roles = {
        "news_analyst": {"findings": [{"text": "Beat estimates", "cites": ["a1"]}]},
        "bull": {"case": "Earnings momentum", "cites": ["a1"]},
        "bear": {"case": "Priced for perfection", "cites": ["a2"]},
        "risk": {"risks": [{"text": "China demand", "cites": ["a1"]}], "invalidated_if": "Next quarter misses"},
    }
    return client


async def finish(client, symbol="AAPL", job=None) -> dict:
    """Queue research (unless `job` is given) and wait for the job, as the company page
    does by polling."""
    job = job or (await client.post("/api/jobs/research", params={"symbol": symbol})).json()
    while job["state"] in ("queued", "running"):
        await asyncio.sleep(0.01)
        job = (await client.get(f"/api/activity/{job['id']}")).json()
    return job


async def researched(client, symbol="AAPL") -> dict:
    await finish(client, symbol)
    return (await client.get(f"/api/companies/{symbol}/research")).json()["run"]


async def test_nothing_is_shown_before_the_first_run(ready):
    assert (await ready.get("/api/companies/AAPL/research")).json() == {"run": None, "failed": None}


async def test_research_runs_on_request_and_its_report_and_trace_come_back(ready):
    job = await finish(ready, "aapl")
    assert (job["state"], job["label"], job["link"]) == ("done", "Research AAPL", "#company/AAPL")
    run = (await ready.get("/api/companies/AAPL/research")).json()["run"]
    assert run["symbol"] == "AAPL" and run["status"] == "done"
    assert run["badge"].startswith("unvalidated")
    report = run["report"]
    assert report["bull"] == {"text": "Earnings momentum", "cites": [1], "role": "bull"}
    assert report["findings"] == [{"text": "Beat estimates", "cites": [1], "role": "news_analyst"}]
    assert report["invalidated_if"] == "Next quarter misses"
    assert [(s["action"], s["thought"]) for s in run["steps"]][0] == ("news_search", "check the news")
    assert run["queries"][:2] == ["Apple shares", "Apple earnings outlook"]


async def test_only_web_addresses_become_links(ready):
    run = await researched(ready)
    links = {e["title"]: e["link"] for e in run["evidence"]}
    assert links["Apple beats estimates"] == "https://reut.rs/1"
    assert links["AAPL stored prices, fundamentals and analyst signals"] is None


async def test_a_run_going_elsewhere_answers_already_running(ready, ctx):
    from oracle.services.jobs_service import single_flight

    with single_flight(ctx, "research", 60):
        job = await finish(ready)
    assert job["state"] == "failed" and "already running" in job["error"]


async def test_a_model_backend_that_is_down_is_a_plain_503(ready, ollama):
    ollama.down = True
    job = await finish(ready)
    assert job["state"] == "failed" and "ollama serve" in job["error"]


async def test_a_failed_retry_keeps_the_last_report_on_screen_and_says_it_failed(ready, ollama):
    await finish(ready)
    ollama.down = True
    await finish(ready)
    body = (await ready.get("/api/companies/AAPL/research")).json()
    assert body["run"]["report"]["bull"]["text"] == "Earnings momentum"
    assert body["failed"]["status"] == "failed" and "ollama serve" in body["failed"]["error"]


async def test_the_stored_data_a_report_cites_comes_with_its_text(ready):
    run = await researched(ready)
    [data] = [e for e in run["evidence"] if e["outlet"] == "stored data"]
    assert data["body"].startswith("AAPL: Apple Inc.")


async def test_a_replay_run_after_a_live_report_is_the_one_shown(ready, ctx, clock, ollama):
    from datetime import date

    from oracle.services.research_service import research

    await finish(ready)
    clock.advance(60)
    ollama.contents = [step("final", "Replayed.")]
    replay = await research(ctx, "AAPL", as_of=date(2026, 1, 5))
    body = (await ready.get("/api/companies/AAPL/research")).json()
    assert body["run"]["id"] == replay.id and body["run"]["as_of"] == "2026-01-05"


async def test_queueing_answers_at_once_with_the_job(ready):
    response = await ready.post("/api/jobs/research", params={"symbol": "AAPL"})
    assert response.status_code == 202 and response.json()["state"] == "queued"


async def test_an_unknown_symbol_is_refused_before_it_queues(ready):
    response = await ready.post("/api/jobs/research", params={"symbol": "NOPE"})
    assert response.status_code == 404 and (await ready.get("/api/activity")).json()["jobs"] == []


async def test_research_is_listed_in_activity_with_its_progress_then_its_outcome(ready):
    job = (await ready.post("/api/jobs/research", params={"symbol": "AAPL"})).json()
    [listed] = (await ready.get("/api/activity")).json()["jobs"]
    assert (listed["id"], listed["kind"]) == (job["id"], "research")
    done = await finish(ready, job=job)
    assert done["state"] == "done" and done["progress"] is None and done["finished_at"]


class Stalled:
    """A model that never answers, so the run stays in flight until cancelled."""

    name, model = "stalled", "stalled"

    def __init__(self):
        self.fell_back_to = set()
        self.asked = threading.Event()  # set on the job's worker thread

    async def reply(self, messages, schema):
        self.asked.set()
        await asyncio.Event().wait()


async def test_cancelling_running_research_records_it_cancelled_frees_the_lock_and_starts_the_next(
        ready, ctx, monkeypatch):
    from oracle.services import activity_service
    from oracle.services.jobs_service import job_statuses
    from oracle.services.research_service import research

    stalled = Stalled()
    monkeypatch.setattr(activity_service, "research", lambda c, s: research(c, s, model=stalled))
    first = (await ready.post("/api/jobs/research", params={"symbol": "AAPL"})).json()
    second = (await ready.post("/api/jobs/research", params={"symbol": "KO"})).json()
    await asyncio.to_thread(stalled.asked.wait)
    cancelled = (await ready.post(f"/api/activity/{first['id']}/cancel")).json()
    assert cancelled["state"] == "cancelled"
    body = (await ready.get("/api/companies/AAPL/research")).json()
    assert body["run"]["status"] == "failed" and body["run"]["error"] == "cancelled"
    await asyncio.sleep(0.05)
    assert (await ready.get(f"/api/activity/{second['id']}")).json()["state"] == "running"
    assert [s.job for s in job_statuses(ctx)] == ["research"]  # held by KO's run, not a leftover
    await ready.post(f"/api/activity/{second['id']}/cancel")


async def test_a_finished_job_refuses_cancel(ready):
    job = await finish(ready)
    response = await ready.post(f"/api/activity/{job['id']}/cancel")
    assert response.status_code == 400 and "already done" in response.json()["error"]


async def test_what_the_checks_rejected_comes_with_the_report(ready, ollama):
    assert (await researched(ready))["report"]["rejections"] is None
    ollama.contents = [step("final", "Again.")]
    ollama.roles["risk"] = "garbled"
    report = (await researched(ready))["report"]
    # This run was shown only its own stored data, so the claims citing a1 and a2 cite nothing it saw.
    assert report["rejections"] == "1 of 6 roles gave an unusable answer; 3 claims dropped (3 citing nothing shown)"


async def test_a_run_with_no_usable_answers_shows_as_failed_beside_the_last_report(ready, ollama):
    await finish(ready)
    ollama.contents = [step("final", "Again.")]
    ollama.roles = dict.fromkeys(("fundamental_analyst", "technical_analyst", "news_analyst", "bull", "bear",
                                  "risk"), "garbled")
    await finish(ready)
    body = (await ready.get("/api/companies/AAPL/research")).json()
    assert body["run"]["report"]["bull"]["text"] == "Earnings momentum"
    assert body["failed"]["error"] == "the model's answers were unusable"


async def test_the_verdict_comes_with_the_report_or_why_there_is_none(ready, ollama):
    ollama.roles["verdict"] = {"verdict": "hold", "confidence": 0.55, "reason": "Beat but priced in", "cites": ["a1"],
                               "invalidated_if": "Guidance is raised"}
    report = (await researched(ready))["report"]
    assert report["verdict"] == {"label": "hold", "confidence": 0.55, "reason": "Beat but priced in", "cites": [1],
                                 "invalidated_if": "Guidance is raised"}
    assert report["no_verdict"] is None and report["did_not_read"] is None
    ollama.contents = [step("final", "Again.")]
    ollama.roles["verdict"] = {"verdict": "moon", "confidence": 1, "reason": "x", "cites": [], "invalidated_if": ""}
    report = (await researched(ready))["report"]
    assert report["verdict"] is None
    assert report["no_verdict"] == "the verdict was rejected: 'moon' is not buy, hold or sell"
