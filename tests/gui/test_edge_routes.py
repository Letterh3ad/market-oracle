from datetime import datetime, timezone

from oracle.research.news import Article
from oracle.services import edges_service
from oracle.store.articles import ArticleRepo
from oracle.store.research import ResearchRepo
from tests.fakes.laya import FakeLaya
from tests.gui.jobs import run_job


def researched(ctx, title):
    ids = ArticleRepo(ctx.conn).save([Article("https://news.test/1", title, "Wire",
                                              datetime(2026, 9, 22, tzinfo=timezone.utc))], "google_news",
                                     "2026-09-23T00:00:00Z")
    repo = ResearchRepo(ctx.conn)
    repo.add_evidence(repo.start("AAPL", "ollama", "m", "2026-09-23T00:00:00Z"), ids)


async def test_finding_ties_is_a_job_and_an_unconfirmed_tie_can_be_confirmed(loaded, ctx, monkeypatch):
    researched(ctx, "Toyota orders chips from Apple")
    monkeypatch.setattr(edges_service, "load_scorer", lambda c: FakeLaya())
    assert (await loaded.get("/api/companies/AAPL/edges")).json() == {"enabled": True, "uncalibrated": True, "edges": []}
    job = await run_job(loaded, "/api/jobs/edges", symbol="AAPL")
    assert (job["kind"], job["state"], job["result"]["unconfirmed"]) == ("edges", "done", 1)
    [tie] = (await loaded.get("/api/companies/AAPL.MI/edges")).json()["edges"]
    assert (tie["other"], tie["relation"], tie["state"], tie["link"]) == ("7203.T", "customer", "unconfirmed",
                                                                          "https://news.test/1")
    confirmed = (await loaded.post(f"/api/companies/AAPL/edges/{tie['id']}/confirm")).json()["confirmed"]
    assert confirmed["state"] == "confirmed"
    again = await loaded.delete(f"/api/companies/AAPL/edges/{tie['id']}")
    assert again.status_code == 400 and "already confirmed" in again.json()["error"]


async def test_a_ties_job_needs_a_symbol_or_a_list(loaded):
    refused = await loaded.post("/api/jobs/edges")
    assert refused.status_code == 400 and "symbol or a list" in refused.json()["error"]
