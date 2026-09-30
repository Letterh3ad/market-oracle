from dataclasses import replace
from datetime import datetime, timezone

from oracle.config import LayaConfig
from oracle.research.laya import MODEL_TAG
from oracle.research.news import Article
from oracle.services import laya_service
from oracle.store.articles import ArticleRepo
from oracle.store.research import ResearchRepo
from tests.fakes.laya import FakeLaya
from tests.gui.jobs import run_job


def researched(ctx):
    ids = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Apple beats estimates", "Wire",
                                              datetime(2026, 9, 22, tzinfo=timezone.utc))], "google_news",
                                     "2026-09-23T00:00:00Z")
    repo = ResearchRepo(ctx.conn)
    repo.add_evidence(repo.start("AAPL", "ollama", "m", "2026-09-23T00:00:00Z"), ids)


async def test_score_with_laya_is_a_job_and_the_company_lists_its_scores_badged_uncalibrated(loaded, ctx, monkeypatch):
    researched(ctx)
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: FakeLaya(tag=MODEL_TAG))
    before = (await loaded.get("/api/companies/AAPL/laya")).json()
    assert (before["model"], before["enabled"], before["uncalibrated"], before["scores"]) == (MODEL_TAG, True, True, [])
    job = await run_job(loaded, "/api/jobs/laya", symbol="aapl")
    assert (job["kind"], job["state"]) == ("laya", "done")
    assert (job["result"]["scored"], job["result"]["articles"]) == (1, 1)
    [s] = (await loaded.get("/api/companies/AAPL.MI/laya")).json()["scores"]
    assert (s["title"], s["link"], s["direction"], s["event"], s["dated"]) == (
        "Apple beats estimates", "https://news.test/1", "up", "earnings", True)


async def test_laya_switched_off_refuses_the_job(loaded, ctx):
    ctx.config = replace(ctx.config, laya=LayaConfig(enabled=False))
    refused = await loaded.post("/api/jobs/laya", params={"symbol": "AAPL"})
    assert refused.status_code == 400 and "Laya is off" in refused.json()["error"]
