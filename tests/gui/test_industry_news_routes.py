from datetime import datetime, timezone

from oracle.research.laya import MODEL_TAG
from oracle.research.news import Article
from oracle.store.articles import ArticleRepo
from oracle.store.research import ResearchRepo
from tests.fakes.laya import FakeLaya
from tests.gui.jobs import run_job


async def test_research_industry_is_a_job_and_the_panel_reads_what_it_scored(loaded, ctx, monkeypatch):
    ids = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Apple beats estimates", "Wire",
                                              datetime(2026, 9, 23, 8, tzinfo=timezone.utc))], "google_news",
                                     "2026-09-23T09:00:00Z")
    repo = ResearchRepo(ctx.conn)
    repo.add_evidence(repo.start("AAPL", "ollama", "m", "2026-09-23T09:00:00Z"), ids)
    monkeypatch.setattr("oracle.services.laya_service.load_scorer", lambda c: FakeLaya(tag=MODEL_TAG))
    job = await run_job(loaded, "/api/jobs/industry", symbol="AAPL")
    assert (job["kind"], job["state"]) == ("industry", "done"), job
    assert (job["result"]["reached"], job["result"]["scored"]) == (1, 1)
    body = (await loaded.get("/api/companies/AAPL/industry-news", params={"days": 3})).json()
    assert body["enabled"] and body["day"] == "2026-09-23" and "uncalibrated" in body["caveat"]
    [event] = body["own"]["events"]
    assert (event["title"], event["direction"], event["link"]) == ("Apple beats estimates", "up", "https://news.test/1")
    assert [(f["relation"], f["score"]) for f in body["features"]] == [("own", 0.8)]
