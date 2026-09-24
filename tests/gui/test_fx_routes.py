import pytest

from tests.gui.conftest import DAYS
from tests.gui.jobs import run_job


@pytest.fixture
async def usd(priced):
    """AAPL priced in USD, which brings its GBP rates in too."""
    assert (await run_job(priced, "/api/jobs/prices", symbol="AAPL"))["state"] == "done"
    return priced


async def test_nothing_is_listed_before_any_foreign_prices(client):
    body = (await client.get("/api/fx")).json()
    assert (body["base"], body["currencies"], body["credits"]) == ("GBP", [], [])
    assert body["sources"][0] == "yahoo"


async def test_each_currency_in_use_shows_its_latest_rate_date_source_and_age(usd):
    [row] = (await usd.get("/api/fx")).json()["currencies"]
    assert (row["currency"], row["units_per_base"], row["date"], row["source"], row["stale"]) == (
        "USD", 1.25, DAYS[-1].isoformat(), "yahoo", False)
    assert row["age_days"] == 1


async def test_an_old_rate_is_flagged_stale(usd, clock):
    clock.advance(7 * 86400)
    [row] = (await usd.get("/api/fx")).json()["currencies"]
    assert row["stale"] and row["age_days"] == 8


async def test_a_currencys_history_comes_oldest_first(usd):
    body = (await usd.get("/api/fx/usd", params={"days": 3})).json()
    assert [r["date"] for r in body["rates"]] == [d.isoformat() for d in DAYS[-3:]]
    assert body["currency"] == "USD" and body["credits"] == []


async def test_open_er_api_rates_carry_its_credit(usd, ctx):
    from datetime import date

    from oracle.market.fx import FxRate
    from oracle.store.fx import FxRepo

    FxRepo(ctx.conn, "GBP").upsert([FxRate("USD", date(2026, 9, 23), 1.26, "open_er_api")], "2026-09-23T12:00:00Z")
    listed = (await usd.get("/api/fx")).json()
    assert listed["currencies"][0]["source"] == "open_er_api"
    assert "exchangerate-api.com" in listed["credits"][0]
    assert "exchangerate-api.com" in (await usd.get("/api/fx/USD")).json()["credits"][0]


async def test_update_runs_as_a_job_and_reports_each_currency(usd):
    job = await run_job(usd, "/api/jobs/fx")
    assert (job["kind"], job["state"]) == ("fx", "done")
    assert job["result"]["updates"] == [{"currency": "USD", "added": 0, "source": "yahoo", "error": None}]


async def test_update_can_use_only_named_sources_and_refuses_unknown_ones(usd):
    job = await run_job(usd, "/api/jobs/fx", source=["yahoo"])
    assert (job["label"], job["state"]) == ("Update FX (yahoo)", "done")
    response = await usd.post("/api/jobs/fx", params={"source": "nope"})
    assert response.status_code == 400 and "known: yahoo" in response.json()["error"]
