import pytest

from oracle.services.jobs_service import single_flight
from oracle.store.health import HealthRepo
from tests.gui.conftest import DAYS, NOW
from tests.gui.jobs import run_job


@pytest.fixture
async def watching(priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    return priced


async def test_an_unpriced_listing_shows_and_says_how_to_price_it(watching):
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert (item["symbol"], item["last_close"], item["lean"]) == ("AAPL", None, None)
    assert item["next"] == "update prices"


async def test_updating_prices_then_analysing_fills_in_price_signals_and_lean(watching):
    prices = (await run_job(watching, "/api/jobs/prices"))["result"]
    aapl = next(l for l in prices["listings"] if l["symbol"] == "AAPL")
    assert aapl == {"symbol": "AAPL", "added": 300, "source": "yahoo", "error": None, "skipped": None}
    # The default benchmarks (VWRL.L, VUSA.L) are priced alongside the watchlist but are not
    # in this fixture's universe, so they are skipped rather than failed.
    assert all(l["skipped"] for l in prices["listings"] if l["symbol"] != "AAPL")
    assert prices["fills"] == []
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["last_date"] == DAYS[-1].isoformat() and not item["stale"] and item["close_base"] is not None
    assert item["next"] == "analyse"

    ran = (await run_job(watching, "/api/jobs/analyse"))["result"]
    assert [(r["symbol"], r["error"]) for r in ran["listings"]] == [("AAPL", None)]
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["lean"]["source"] == "baseline" and not item["lean"]["stale"] and item["next"] is None
    trend = next(s for s in item["signals"] if s["analyst"] == "trend")
    assert trend == {"analyst": "trend", "direction": "bullish", "stale": False}


async def test_a_job_another_process_holds_answers_already_running(ctx, watching):
    with single_flight(ctx, "prices_update", 60.0):
        job = await run_job(watching, "/api/jobs/prices")
    assert job["state"] == "failed" and "already running" in job["error"]


async def test_removing_a_listing_empties_the_list(watching):
    assert (await watching.delete("/api/watchlists/main/items/AAPL")).json() == {"removed": True}
    assert (await watching.get("/api/watchlists/main")).json()["items"] == []


async def test_a_listing_gone_from_the_universe_is_flagged_not_an_error(ctx, watching):
    with ctx.conn:
        ctx.conn.execute("DELETE FROM listings WHERE symbol = 'AAPL'")
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["missing"] and item["next"] == "remove it, or refresh the universe later"


async def test_a_new_list_is_created_then_a_listing_added_to_it(priced):
    created = await priced.post("/api/watchlists", json={"name": "Dividends"})
    assert created.json() == {"name": "Dividends", "created": True}
    assert [w["name"] for w in (await priced.get("/api/watchlists")).json()] == ["Dividends"]
    await priced.post("/api/watchlists/Dividends/items", json={"symbol": "AAPL"})
    [item] = (await priced.get("/api/watchlists/dividends")).json()["items"]
    assert item["symbol"] == "AAPL" and (await priced.get("/api/watchlists/main")).json()["items"] == []


async def test_a_list_name_too_long_is_refused(priced):
    response = await priced.post("/api/watchlists", json={"name": "x" * 41})
    assert response.status_code == 400 and "at most 40" in response.json()["error"]
    assert (await priced.get("/api/watchlists")).json() == []


async def test_a_dead_listing_shows_its_live_siblings_and_swaps_to_one(ctx, watching):
    HealthRepo(ctx.conn).note_no_data("AAPL", NOW.date(), 1)
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["dead_since"] == NOW.date().isoformat()
    assert [s["symbol"] for s in item["siblings"]] == ["APC.DE", "AAPL.MI"]
    company = (await watching.get("/api/companies/AAPL", params={"refresh": "false"})).json()
    assert company["siblings"] == item["siblings"] and company["watchlists"] == ["main"]

    swapped = await watching.post("/api/watchlists/swap", json={"old": "AAPL", "new": "APC.DE"})
    assert swapped.json() == {"old": "AAPL", "new": "APC.DE", "watchlists": ["main"], "held_by": []}
    [item] = (await watching.get("/api/watchlists/main")).json()["items"]
    assert item["symbol"] == "APC.DE" and item["dead_since"] is None and item["siblings"] == []


async def test_a_swap_to_another_company_is_refused(watching):
    refused = await watching.post("/api/watchlists/swap", json={"old": "AAPL", "new": "KO"})
    assert refused.status_code == 400 and "not a listing of Apple" in refused.json()["error"]
