from datetime import date, datetime, timezone

from oracle.research.news import Article
from oracle.services.watch_service import watch
from oracle.store.articles import ArticleRepo
from oracle.store.history import HistoryRepo, Window


async def test_coverage_lists_each_company_of_the_list_with_its_quarters_and_search_name(loaded, ctx):
    watch(ctx, "AAPL")
    [a] = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Apple sells phones", "Wire",
                                              datetime(2025, 5, 2, tzinfo=timezone.utc))], "google_news",
                                     "2026-09-23T00:00:00Z")
    HistoryRepo(ctx.conn).finish(Window("AAPL", "google_news", date(2025, 4, 1), date(2025, 7, 1), 1, 1, 0, 0,
                                        on_topic=1), [a], "2026-09-23T00:00:00Z")
    body = (await loaded.get("/api/history/coverage?fresh=true")).json()
    assert body["start"] == "2022-09-01" and body["quarters"][0] == "2022-Q3" and body["thin_below"] == 5
    assert body["last_run"] is None and body["noisy_below"] == 0.5
    apple = next(r for r in body["rows"] if r["symbol"] == "AAPL")
    assert apple["main"] and apple["cells"]["2025-Q2"] == {"news": 1, "filings": 0, "outlets": 1, "on_topic": 1.0}
    assert "2025-Q2" in apple["thin"] and apple["noisy"] == [] and body["windows_done"] == 1
    assert apple["search"]["query"] == '"Apple"' and apple["search"]["source"] == "seed"


async def test_a_search_name_is_set_and_cleared_and_the_card_shows_it_at_once(loaded, ctx):
    watch(ctx, "KO")
    await loaded.get("/api/history/coverage")
    put = await loaded.put("/api/history/names/KO", json={"query": "Coca-Cola", "aliases": ["Coke"]})
    assert put.status_code == 200 and put.json() == {"symbol": "KO", "forgotten": 0}
    ko = next(r for r in (await loaded.get("/api/history/coverage")).json()["rows"] if r["symbol"] == "KO")
    assert ko["search"]["query"] == '"Coca-Cola"' and ko["search"]["aliases"] == ["Coke"]
    assert (await loaded.put("/api/history/names/KO", json={})).status_code == 400
    assert (await loaded.delete("/api/history/names/KO")).status_code == 200
    assert (await loaded.delete("/api/history/names/KO")).status_code == 400


async def test_backfill_history_is_queued_as_one_job_and_pressed_again_joins_it(loaded, ctx):
    watch(ctx, "AAPL")
    first = await loaded.post("/api/jobs/history")
    again = await loaded.post("/api/jobs/history?watchlist=main")
    assert first.status_code == 202 and first.json()["kind"] == "history"
    assert again.json()["id"] == first.json()["id"]
    assert (await loaded.post("/api/jobs/history?watchlist=nope")).status_code == 404


async def test_a_search_name_is_refused_while_the_backfill_runs(loaded, ctx):
    from oracle.services.history_service import JOB, LEASE_S
    from oracle.services.jobs_service import single_flight

    watch(ctx, "KO")
    with single_flight(ctx, JOB, LEASE_S):
        put = await loaded.put("/api/history/names/KO", json={"query": "Coca-Cola"})
    assert put.status_code == 409 and "cancel it" in put.json()["error"]
