import pytest

from tests.gui.conftest import DAYS


@pytest.fixture
async def analysed(priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    await priced.post("/api/jobs/prices")
    return priced


async def test_the_view_shows_the_listing_and_its_price_and_asks_for_analysis(analysed):
    body = (await analysed.get("/api/companies/aapl")).json()
    assert (body["profile"]["symbol"], body["profile"]["name"]) == ("AAPL", "Apple Inc.")
    assert body["price"]["date"] == DAYS[-1].isoformat() and body["price"]["currency"] == "USD"
    assert body["price"]["close_base"] == pytest.approx(body["price"]["close"] / 1.25)
    assert (body["signals"], body["lean"], body["next"]) == ([], None, "analyse")


async def test_after_analysis_each_signal_explains_itself_and_the_lean_is_current(analysed):
    await analysed.post("/api/jobs/analyse")
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
    prices = (await priced.post("/api/jobs/prices", params={"symbol": "aapl"})).json()
    assert prices["listings"] == [{"symbol": "AAPL", "added": 300, "source": "yahoo", "error": None}]
    ran = (await priced.post("/api/jobs/analyse", params={"symbol": "aapl"})).json()
    assert ran["listings"][0]["symbol"] == "AAPL" and ran["listings"][0]["lean"]["label"]
    assert (await priced.get("/api/watchlists/main")).json()["items"] == []


@pytest.mark.parametrize(("path", "job"), [("/api/jobs/prices", "prices_update"), ("/api/jobs/analyse", "analyse")])
async def test_a_single_listing_job_waits_its_turn_like_the_watchlist_one(ctx, priced, path, job):
    from oracle.services.jobs_service import single_flight

    with single_flight(ctx, job, 60.0):
        response = await priced.post(path, params={"symbol": "AAPL"})
    assert response.status_code == 409 and "already running" in response.json()["error"]
