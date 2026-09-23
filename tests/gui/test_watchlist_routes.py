import pytest

from oracle.services.jobs_service import single_flight
from tests.gui.conftest import DAYS


@pytest.fixture
async def watching(priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    return priced


async def test_an_unpriced_listing_shows_and_says_how_to_price_it(watching):
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert (item["symbol"], item["last_close"], item["lean"]) == ("AAPL", None, None)
    assert item["next"] == "update prices"


async def test_updating_prices_then_analysing_fills_in_price_signals_and_lean(watching):
    prices = (await watching.post("/api/jobs/prices")).json()
    assert prices["listings"] == [{"symbol": "AAPL", "added": 300, "source": "yahoo", "error": None, "skipped": None}]
    assert prices["fills"] == []
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["last_date"] == DAYS[-1].isoformat() and not item["stale"] and item["close_base"] is not None
    assert item["next"] == "analyse"

    ran = (await watching.post("/api/jobs/analyse")).json()
    assert [(r["symbol"], r["error"]) for r in ran["listings"]] == [("AAPL", None)]
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["lean"]["source"] == "baseline" and not item["lean"]["stale"] and item["next"] is None
    trend = next(s for s in item["signals"] if s["analyst"] == "trend")
    assert trend == {"analyst": "trend", "direction": "bullish", "stale": False}


async def test_a_job_another_process_holds_answers_already_running(ctx, watching):
    with single_flight(ctx, "prices_update", 60.0):
        response = await watching.post("/api/jobs/prices")
    assert response.status_code == 409 and "already running" in response.json()["error"]


async def test_removing_a_listing_empties_the_list(watching):
    assert (await watching.delete("/api/watchlists/main/items/AAPL")).json() == {"removed": True}
    assert (await watching.get("/api/watchlists/main")).json()["items"] == []


async def test_a_listing_gone_from_the_universe_is_flagged_not_an_error(ctx, watching):
    with ctx.conn:
        ctx.conn.execute("DELETE FROM listings WHERE symbol = 'AAPL'")
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["missing"] and item["next"] == "remove it, or refresh the universe later"
