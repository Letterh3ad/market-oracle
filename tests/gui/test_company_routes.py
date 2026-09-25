import pytest

from tests.gui.conftest import DAYS
from tests.gui.jobs import run_job


@pytest.fixture
async def analysed(priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    await run_job(priced, "/api/jobs/prices")
    return priced


async def test_the_view_shows_the_listing_and_its_price_and_asks_for_analysis(analysed):
    body = (await analysed.get("/api/companies/aapl")).json()
    assert (body["profile"]["symbol"], body["profile"]["name"]) == ("AAPL", "Apple Inc.")
    assert body["price"]["date"] == DAYS[-1].isoformat() and body["price"]["currency"] == "USD"
    assert body["price"]["close_base"] == pytest.approx(body["price"]["close"] / 1.25)
    assert (body["signals"], body["lean"], body["next"]) == ([], None, "analyse")


async def test_after_analysis_each_signal_explains_itself_and_the_lean_is_current(analysed):
    await run_job(analysed, "/api/jobs/analyse")
    body = (await analysed.get("/api/companies/AAPL")).json()
    trend = next(s for s in body["signals"] if s["analyst"] == "trend")
    assert trend["direction"] == "bullish" and trend["reason"] and not trend["stale"]
    for s in body["signals"]:
        for f in s["figures"]:
            assert f["meaning"] != "No explanation written for this figure yet.", (s["analyst"], f["name"])
    assert {f["name"] for f in trend["figures"]} == {"close", "sma50", "sma200", "momentum_12_1"}
    assert body["lean"]["source"] == "baseline" and not body["lean"]["stale"] and body["next"] is None


async def test_the_price_series_comes_in_date_order_with_base_currency_closes(analysed):
    body = (await analysed.get("/api/companies/AAPL/prices", params={"days": 30})).json()
    assert (body["currency"], body["base_currency"], body["credits"]) == ("USD", "GBP", [])
    rows = body["rows"]
    assert len(rows) == 30 and rows[-1]["date"] == DAYS[-1].isoformat()
    assert rows[-1]["close_base"] == pytest.approx(rows[-1]["close"] / 1.25)


async def test_peers_name_their_basis_and_the_count_can_be_changed(analysed, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = (await analysed.get("/api/companies/AAPL/peers")).json()
    assert before["count"] == 20 and before["range"] == [5, 100] and before["basis"]
    saved = (await analysed.put("/api/settings/peers", json={"count": 30})).json()
    assert saved["count"] == 30 and saved["path"].endswith("oracle.toml") and "analyse" in saved["note"].lower()
    assert (await analysed.get("/api/companies/AAPL/peers")).json()["count"] == 30
    assert (await analysed.put("/api/settings/peers", json={"count": 3})).status_code == 400


async def test_an_unknown_company_is_a_404_with_suggestions(loaded):
    response = await loaded.get("/api/companies/APPL")
    assert response.status_code == 404 and "AAPL" in response.json()["error"]


async def test_one_listing_can_be_priced_and_analysed_without_being_watched(priced):
    prices = (await run_job(priced, "/api/jobs/prices", symbol="aapl"))["result"]
    assert prices["listings"] == [{"symbol": "AAPL", "added": 300, "source": "yahoo", "error": None, "skipped": None}]
    ran = (await run_job(priced, "/api/jobs/analyse", symbol="aapl"))["result"]
    assert ran["listings"][0]["symbol"] == "AAPL" and ran["listings"][0]["lean"]["label"]
    assert (await priced.get("/api/watchlists/main")).json()["items"] == []


@pytest.mark.parametrize(("path", "job"), [("/api/jobs/prices", "prices_update"), ("/api/jobs/analyse", "analyse")])
async def test_a_single_listing_job_waits_its_turn_like_the_watchlist_one(ctx, priced, path, job):
    from oracle.services.jobs_service import single_flight

    with single_flight(ctx, job, 60.0):
        job = await run_job(priced, path, symbol="AAPL")
    assert job["state"] == "failed" and "already running" in job["error"]


async def test_a_dead_listing_says_so_and_check_again_clears_it(ctx, priced):
    from datetime import date

    from oracle.store.health import HealthRepo

    HealthRepo(ctx.conn).note_no_data("AAPL", date(2026, 9, 1), dead_after=1)
    assert (await priced.get("/api/companies/AAPL")).json()["dead_since"] == "2026-09-01"
    hits = (await priced.get("/api/universe/search", params={"q": "AAPL"})).json()["hits"]
    assert next(h for h in hits if h["symbol"] == "AAPL")["dead"]
    await run_job(priced, "/api/jobs/prices", symbol="AAPL")  # Check again: data is back
    assert (await priced.get("/api/companies/AAPL")).json()["dead_since"] is None


async def test_events_list_moves_after_stored_earnings_dates(ctx, analysed):
    import json

    from oracle.store.meta import MetaRepo

    MetaRepo(ctx.conn).set("earnings_dates:AAPL", json.dumps({
        "source": "SEC 8-K item 2.02", "fetched_at": "2026-09-23T00:00:00+00:00",
        "releases": [[DAYS[-3].isoformat(), False]]}))
    body = (await analysed.get("/api/companies/aapl/events")).json()
    assert (body["symbol"], body["source"], body["horizons"]) == ("AAPL", "SEC 8-K item 2.02", [1, 5, 20])
    event = body["events"][0]
    assert (event["day"], event["before_open"], event["base"]) == (DAYS[-3].isoformat(), False, DAYS[-3].isoformat())
    assert event["windows"][0]["move"] == pytest.approx(0.001) and event["windows"][2]["move"] is None
    assert body["headline"] == "after the last 1 earnings: median +0.1% next day, 1 of 1 up"


async def test_events_without_a_sec_contact_say_so(analysed):
    body = (await analysed.get("/api/companies/AAPL/events?refresh=true")).json()
    assert body["events"] == [] and "contact" in body["note"]
