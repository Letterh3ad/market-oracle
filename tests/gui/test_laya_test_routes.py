from datetime import datetime, timezone

from oracle.research.laya import MODEL_TAG
from oracle.research.news import Article
from oracle.services import laya_service
from oracle.services.watch_service import watch
from oracle.store.articles import ArticleRepo
from oracle.store.history import HistoryRepo, Window
from tests.fakes.laya import FakeLaya
from tests.gui.jobs import run_job


async def test_scoring_the_history_and_the_test_are_jobs_whose_results_the_card_reads(loaded, ctx, monkeypatch):
    watch(ctx, "AAPL")
    ids = ArticleRepo(ctx.conn).save([Article("https://n.test/a", "Apple beats estimates", "Wire",
                                              datetime(2023, 1, 3, 12, tzinfo=timezone.utc))], "google_news", "x")
    HistoryRepo(ctx.conn).finish(Window("AAPL", "google_news", datetime(2023, 1, 1).date(), datetime(2023, 4, 1).date(),
                                        1, 1, 0, 0), ids, "x")
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: FakeLaya(tag=MODEL_TAG, device="cuda"))
    assert (await loaded.get("/api/laya/tests")).json() == {"tests": [], "last_scoring": None, "models": [
        "laya-multilingual@e4e9ddf", "ollama:qwen2.5:32b"]}
    plan = (await loaded.get("/api/laya/plan")).json()
    assert (plan["targets"], plan["unscored"]) == (1, 1)
    job = await run_job(loaded, "/api/jobs/laya-history")
    assert (job["kind"], job["state"], job["result"]["scored"]) == ("laya_history", "done", 1)
    test = await run_job(loaded, "/api/jobs/laya-test", sensitivity="true")
    assert (test["kind"], test["state"], test["result"]["run"]) == ("laya_test", "done", "sensitivity")
    got = (await loaded.get("/api/laya/tests")).json()
    assert [t["run"] for t in got["tests"]] == ["sensitivity"] and got["tests"][0]["verdict"] == "INCONCLUSIVE"
    assert got["tests"][0]["cutoff"] == "2025-05-31" and got["tests"][0]["standing"].startswith("Laya's FAIL stands")
    assert "1 pairs scored" in got["last_scoring"]["summary"]
