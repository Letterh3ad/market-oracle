from datetime import date, datetime, timezone

from oracle.research.news import Article
from oracle.services.watch_service import watch
from oracle.store.articles import ArticleRepo
from oracle.store.history import HistoryRepo, Window


async def test_coverage_lists_each_company_of_the_list_with_its_quarters(loaded, ctx):
    watch(ctx, "AAPL")
    [a] = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Apple sells phones", "Wire",
                                              datetime(2025, 5, 2, tzinfo=timezone.utc))], "google_news",
                                     "2026-09-23T00:00:00Z")
    HistoryRepo(ctx.conn).finish(Window("AAPL", "google_news", date(2025, 4, 1), date(2025, 7, 1), 1, 1, 0, 0), [a],
                                 "2026-09-23T00:00:00Z")
    body = (await loaded.get("/api/history/coverage")).json()
    assert body["start"] == "2022-09-01" and body["quarters"][0] == "2022-Q3" and body["thin_below"] == 5
    apple = next(r for r in body["rows"] if r["symbol"] == "AAPL")
    assert apple["main"] and apple["cells"]["2025-Q2"] == {"news": 1, "filings": 0, "outlets": 1}
    assert "2025-Q2" in apple["thin"] and body["windows_done"] == 1


async def test_backfill_history_is_queued_as_one_job_and_pressed_again_joins_it(loaded, ctx):
    watch(ctx, "AAPL")
    first = await loaded.post("/api/jobs/history")
    again = await loaded.post("/api/jobs/history?watchlist=main")
    assert first.status_code == 202 and first.json()["kind"] == "history"
    assert again.json()["id"] == first.json()["id"]
    assert (await loaded.post("/api/jobs/history?watchlist=nope")).status_code == 404
