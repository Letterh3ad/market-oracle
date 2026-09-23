from dataclasses import replace
from datetime import timedelta

from oracle.services.paper_service import place_buy, strategy_step
from tests.fakes.yahoo import bar


async def test_both_accounts_start_level_side_by_side(loaded):
    body = (await loaded.get("/api/ledgers")).json()
    assert body["base_currency"] == "GBP"
    assert [a["name"] for a in body["accounts"]] == ["strategy", "manual"]
    for a in body["accounts"]:
        assert (a["equity"], a["start"], a["return"], a["cash"]) == (10_000.0, 10_000.0, 0.0, 10_000.0)
        assert (a["positions"], a["pending"], a["sells"], a["history"]) == ([], [], [], [])


async def test_a_queued_buy_shows_as_pending_on_its_account(ctx, loaded):
    place_buy(ctx, "AAPL", 1000.0)
    manual = (await loaded.get("/api/ledgers")).json()["accounts"][1]
    [order] = manual["pending"]
    assert (order["symbol"], order["side"], order["cash"], order["status"]) == ("AAPL", "buy", 1000.0, "pending")


async def test_a_filled_buy_shows_as_a_position_with_an_equity_record(ctx, priced, yahoo, clock):
    await priced.post("/api/jobs/prices", params={"symbol": "AAPL"})
    place_buy(ctx, "AAPL", 1000.0)
    # It fills at the next open: the day after it was placed.
    tomorrow = (clock.utcnow() + timedelta(days=1)).date().isoformat()
    yahoo.series["AAPL"].bars.append(bar(tomorrow, 150.0))
    yahoo.series["GBPUSD=X"].bars.append(bar(tomorrow, 1.25))
    clock.advance(2 * 86400)  # past that session's close, so its bar counts as complete
    run = (await priced.post("/api/jobs/prices", params={"symbol": "AAPL"})).json()
    assert [f["status"] for f in run["fills"]] == ["filled"]
    manual = (await priced.get("/api/ledgers")).json()["accounts"][1]
    [position] = manual["positions"]
    assert position["symbol"] == "AAPL" and position["shares"] > 0 and manual["pending"] == []
    assert manual["cash"] < 10_000.0 and manual["costs"] > 0
    # One equity record per day a fill run happened: the first price run and this one.
    assert [day for day, _ in manual["history"]] == ["2026-09-23", clock.utcnow().date().isoformat()]


async def test_the_decision_log_lists_the_strategy_steps_newest_first(ctx, priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    strategy_step(ctx)
    [d] = (await priced.get("/api/ledgers/decisions")).json()
    assert (d["symbol"], d["action"], d["reason"]) == ("AAPL", "skip", "no lean yet: run `oracle analyse`")


async def test_ledgers_carry_the_credits_their_rates_require(ctx, loaded, monkeypatch):
    from oracle.gui.routes import ledgers

    real = ledgers.account_view
    monkeypatch.setattr(ledgers, "account_view", lambda c, name: replace(real(c, name), rate_sources=("open_er_api",)))
    body = (await loaded.get("/api/ledgers")).json()
    assert body["credits"] == ["Rates By Exchange Rate API (https://www.exchangerate-api.com)"]


async def test_the_strategy_rules_shown_are_the_configured_ones(ctx, loaded):
    ctx.config = replace(ctx.config, strategy=replace(ctx.config.strategy, max_positions=7))
    rules = (await loaded.get("/api/ledgers")).json()["rules"]
    assert rules == {"min_probability": 0.6, "max_positions": 7, "max_position": 0.1, "cash_floor": 0.2,
                     "stop_loss": 0.08}
