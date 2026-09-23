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


async def test_nothing_is_shown_before_the_first_run(ready):
    assert (await ready.get("/api/companies/AAPL/research")).json() == {"run": None}


async def test_research_runs_on_request_and_its_report_and_trace_come_back(ready):
    response = await ready.post("/api/jobs/research", params={"symbol": "aapl"})
    run = response.json()["run"]
    assert response.status_code == 200 and run["symbol"] == "AAPL" and run["status"] == "done"
    assert run["badge"].startswith("unvalidated")
    report = run["report"]
    assert report["bull"] == {"text": "Earnings momentum", "cites": [1], "role": "bull"}
    assert report["findings"] == [{"text": "Beat estimates", "cites": [1], "role": "news_analyst"}]
    assert report["invalidated_if"] == "Next quarter misses"
    assert [(s["action"], s["thought"]) for s in run["steps"]][0] == ("news_search", "check the news")
    assert run["queries"][:2] == ["Apple shares", "Apple earnings outlook"]
    assert (await ready.get("/api/companies/AAPL/research")).json()["run"] == run


async def test_only_web_addresses_become_links(ready):
    run = (await ready.post("/api/jobs/research", params={"symbol": "AAPL"})).json()["run"]
    links = {e["title"]: e["link"] for e in run["evidence"]}
    assert links["Apple beats estimates"] == "https://reut.rs/1"
    assert links["AAPL stored prices, fundamentals and analyst signals"] is None


async def test_a_run_going_elsewhere_answers_already_running(ready, ctx):
    from oracle.services.jobs_service import single_flight

    with single_flight(ctx, "research", 60):
        response = await ready.post("/api/jobs/research", params={"symbol": "AAPL"})
    assert response.status_code == 409 and "already running" in response.json()["error"]


async def test_a_model_backend_that_is_down_is_a_plain_503(ready, ollama):
    ollama.down = True
    response = await ready.post("/api/jobs/research", params={"symbol": "AAPL"})
    assert response.status_code == 503 and "ollama serve" in response.json()["error"]
