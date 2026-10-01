from datetime import timedelta

import pytest

from tests.fakes.yahoo import bar
from tests.gui.jobs import run_job


async def test_a_buy_queues_on_the_manual_account(loaded):
    order = (await loaded.post("/api/ledgers/manual/buy", json={"symbol": "aapl", "amount": 1500})).json()
    assert (order["account"], order["symbol"], order["side"], order["cash"], order["status"]) == (
        "manual", "AAPL", "buy", 1500.0, "pending")


@pytest.mark.parametrize(("body", "message"), [
    ({"symbol": "AAPL", "amount": 0}, "more than nothing"),
    ({"symbol": "APPL", "amount": 100}, "AAPL"),
    ({"symbol": "AAPL", "amount": 100, "account": "strategy"}, "account"),
])
async def test_a_bad_buy_is_refused_with_the_reason(loaded, body, message):
    response = await loaded.post("/api/ledgers/manual/buy", json=body)
    assert response.status_code in (400, 404) and message in response.json()["error"]


async def test_selling_what_is_not_held_is_refused(loaded):
    response = await loaded.post("/api/ledgers/manual/sell", json={"symbol": "AAPL", "shares": None})
    assert response.status_code == 400 and "not held" in response.json()["error"]


async def test_fill_now_buys_at_the_next_open_then_a_sell_of_some_shares_queues(ctx, priced, yahoo, clock):
    await run_job(priced, "/api/jobs/prices", symbol="AAPL")
    await priced.post("/api/ledgers/manual/buy", json={"symbol": "AAPL", "amount": 1000})
    [waiting] = (await priced.post("/api/ledgers/fill")).json()  # no later bar yet
    assert (waiting["symbol"], waiting["status"]) == ("AAPL", "pending")
    assert waiting["note"] == "waiting for the next opening price after it was placed"
    tomorrow = (clock.utcnow() + timedelta(days=1)).date().isoformat()
    yahoo.series["AAPL"].bars.append(bar(tomorrow, 150.0))
    yahoo.series["GBPUSD=X"].bars.append(bar(tomorrow, 1.25))
    clock.advance(2 * 86400)
    await run_job(priced, "/api/jobs/prices", symbol="AAPL")  # fills on the way in
    [position] = (await priced.get("/api/ledgers")).json()["accounts"][3]["positions"]
    sell = (await priced.post("/api/ledgers/manual/sell", json={"symbol": "AAPL", "shares": 2})).json()
    assert (sell["side"], sell["shares"], position["shares"] > 2) == ("sell", 2, True)
    too_many = await priced.post("/api/ledgers/manual/sell", json={"symbol": "AAPL", "shares": 10_000})
    assert too_many.status_code == 400 and "can sell 1 to" in too_many.json()["error"]


async def test_a_strategy_step_runs_the_rules_and_says_why_for_each_listing(priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    body = (await priced.post("/api/ledgers/step")).json()
    assert body["off"] is None
    [d] = body["decisions"]
    assert (d["symbol"], d["action"], d["reason"]) == ("AAPL", "skip", "no lean yet: analyse it first")
    assert [x["symbol"] for x in (await priced.get("/api/ledgers/decisions")).json()] == ["AAPL"]


@pytest.mark.frozen_lines
async def test_a_frozen_strategy_step_decides_nothing_and_says_so(priced):
    from oracle.services.paper_service import frozen_note

    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    assert (await priced.post("/api/ledgers/step")).json() == {"off": frozen_note("strategy"), "decisions": []}
    assert (await priced.get("/api/ledgers/decisions")).json() == []


async def test_index_step_says_why_it_bought_nothing_without_acwi_in_the_universe(priced):
    [d] = (await priced.post("/api/ledgers/index/step")).json()
    assert (d["symbol"], d["action"]) == ("ACWI", "skip") and "refresh the universe" in d["reason"]
    assert [x["symbol"] for x in (await priced.get("/api/ledgers/decisions?account=index")).json()] == ["ACWI"]
