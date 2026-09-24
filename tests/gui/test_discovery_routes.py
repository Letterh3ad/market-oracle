from datetime import datetime, timezone

import pytest

from oracle.research.news import Article
from oracle.services import discovery_service
from oracle.store.articles import ArticleRepo
from oracle.store.discoveries import DiscoveryRepo
from tests.fakes.llm import ScriptedModel
from tests.gui.jobs import run_job
from tests.fakes.yahoo import bar


@pytest.fixture
async def seeded(loaded, ctx, yahoo, monkeypatch):
    """7203.T watched; Toyota's peers in the fake universe are priced."""
    monkeypatch.setattr(discovery_service, "configured_model", lambda c: ScriptedModel())
    for s in ("7203.T", "TM"):
        yahoo.add(s, "JPY", "Asia/Tokyo", [bar("2026-09-21", 10.0), bar("2026-09-22", 11.0)])
    await loaded.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    return loaded


async def test_discover_now_runs_as_a_job_and_says_what_it_did(seeded):
    job = await run_job(seeded, "/api/jobs/discover")
    assert job["state"] == "done" and job["kind"] == "discover" and job["link"] == "#watchlist"
    result = job["result"]
    assert set(result) >= {"added", "skipped", "seeds", "source_errors", "note"}
    assert result["seeds"] == ["AAPL"]


async def test_the_list_shows_why_with_evidence_links_and_remove_and_move_work(seeded, ctx):
    [a] = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Toyota and Apple talk", "Wire",
                                              datetime(2026, 9, 22, tzinfo=timezone.utc)),
                                      ], "google_news", "2026-09-23T00:00:00Z")
    repo = DiscoveryRepo(ctx.conn)
    repo.add("7203.T", "k1", "Toyota Motor Corporation", "related", "AAPL", "partner", [a], "2026-09-23T00:00:00Z")
    repo.add("KO", "k2", "The Coca-Cola Company", "related", "AAPL", "peer", [], "2026-09-23T00:00:00Z")
    body = (await seeded.get("/api/discovery")).json()
    assert body["settings"] == {"enabled": True, "per_tick": 1, "max_total": 10, "never": ["Tesla"],
                                "ai_trades": True}
    first = body["items"][0]
    assert (first["symbol"], first["why"]) == ("7203.T", "named as a partner of AAPL in the news")
    [e] = first["evidence"]
    assert (e["id"], e["title"], e["link"], e["outlet"]) == (a, "Toyota and Apple talk", "https://news.test/1", "Wire")

    moved = await seeded.post("/api/discovery/7203.T/move")
    assert moved.status_code == 200 and moved.json()["watchlist"] == "main"
    removed = await seeded.delete("/api/discovery/KO")
    assert removed.json() == {"removed": True}
    assert (await seeded.get("/api/discovery")).json()["items"] == []
    assert repo.removed()[0] == {"KO"}
    main = [i["symbol"] for i in (await seeded.get("/api/watchlists/main")).json()["items"]]
    assert "7203.T" in main
