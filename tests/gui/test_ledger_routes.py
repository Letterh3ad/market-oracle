import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from oracle.services.paper_service import place_buy, strategy_step
from tests.fakes.yahoo import bar
from tests.gui.jobs import run_job


async def test_every_account_starts_level_side_by_side(loaded):
    body = (await loaded.get("/api/ledgers")).json()
    assert body["base_currency"] == "GBP"
    assert [a["name"] for a in body["accounts"]] == ["strategy", "ai", "manual"]
    for a in body["accounts"]:
        assert (a["equity"], a["start"], a["return"], a["cash"]) == (10_000.0, 10_000.0, 0.0, 10_000.0)
        assert (a["positions"], a["pending"], a["sells"], a["history"]) == ([], [], [], [])
    assert body["discovery"].startswith("Discovery: 0 discovered companies, traded by the AI account")


async def test_a_queued_buy_shows_as_pending_on_its_account(ctx, loaded):
    place_buy(ctx, "AAPL", 1000.0)
    manual = (await loaded.get("/api/ledgers")).json()["accounts"][2]
    [order] = manual["pending"]
    assert (order["symbol"], order["side"], order["cash"], order["status"]) == ("AAPL", "buy", 1000.0, "pending")


async def test_a_filled_buy_shows_as_a_position_with_an_equity_record(ctx, priced, yahoo, clock):
    await run_job(priced, "/api/jobs/prices", symbol="AAPL")
    place_buy(ctx, "AAPL", 1000.0)
    # It fills at the next open: the day after it was placed.
    tomorrow = (clock.utcnow() + timedelta(days=1)).date().isoformat()
    yahoo.series["AAPL"].bars.append(bar(tomorrow, 150.0))
    yahoo.series["GBPUSD=X"].bars.append(bar(tomorrow, 1.25))
    clock.advance(2 * 86400)  # past that session's close, so its bar counts as complete
    run = (await run_job(priced, "/api/jobs/prices", symbol="AAPL"))["result"]
    assert [f["status"] for f in run["fills"]] == ["filled"]
    manual = (await priced.get("/api/ledgers")).json()["accounts"][2]
    [position] = manual["positions"]
    assert position["symbol"] == "AAPL" and position["shares"] > 0 and manual["pending"] == []
    assert manual["cash"] < 10_000.0 and manual["costs"] > 0
    # One equity record per day a fill run happened: the first price run and this one.
    assert [day for day, _ in manual["history"]] == ["2026-09-23", clock.utcnow().date().isoformat()]


async def test_the_decision_log_lists_the_strategy_steps_newest_first(ctx, priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    strategy_step(ctx)
    [d] = (await priced.get("/api/ledgers/decisions")).json()
    assert (d["symbol"], d["action"], d["reason"]) == ("AAPL", "skip", "no lean yet: analyse it first")


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


async def test_the_ai_account_is_off_until_switched_on_and_then_steps_on_verdicts(ctx, priced):
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    assert (await priced.get("/api/ledgers")).json()["ai_account"] == {
        "enabled": False, "max_verdict_age_days": 7, "move_threshold": 0.05, "research_budget_min": 30}
    off = (await priced.post("/api/ledgers/ai/step")).json()
    assert off["decisions"] == [] and off["off"].startswith("the AI account is off")
    assert (await priced.put("/api/ledgers/ai", json={"enabled": True})).json() == {"enabled": True}
    assert ctx.config.ai_account.enabled is True
    on = (await priced.post("/api/ledgers/ai/step")).json()
    assert on["off"] is None and on["badge"].startswith("unvalidated: AI research")
    assert [(d["symbol"], d["action"], d["reason"], d["needs_research"]) for d in on["decisions"]] == [
        ("AAPL", "skip", "no verdict yet: run research on it", True)]
    [d] = (await priced.get("/api/ledgers/decisions", params={"account": "ai"})).json()
    assert (d["account"], d["symbol"], d["report_id"], d["needs_research"]) == ("ai", "AAPL", None, True)
    assert (await priced.get("/api/ledgers/decisions")).json() == []


async def test_the_ai_switch_and_decision_log_refuse_what_they_do_not_know(loaded):
    assert (await loaded.put("/api/ledgers/ai", json={"enabled": "yes"})).status_code in (400, 422)
    assert (await loaded.get("/api/ledgers/decisions", params={"account": "manual"})).status_code in (400, 422)


async def test_the_ai_research_settings_are_saved_and_refused_out_of_range(ctx, loaded):
    body = {"max_verdict_age_days": 10, "move_threshold": 0.08, "research_budget_min": 20}
    saved = (await loaded.put("/api/ledgers/ai/research", json=body)).json()
    assert saved == {"enabled": False, **body}
    assert (await loaded.get("/api/ledgers")).json()["ai_account"] == saved
    refused = await loaded.put("/api/ledgers/ai/research", json={**body, "research_budget_min": 0})
    assert refused.status_code == 400 and "research_budget_min" in refused.json()["error"]
    assert ctx.config.ai_account.research_budget_min == 20


async def test_the_comparison_waits_for_the_ai_account_history(loaded):
    compare = (await loaded.get("/api/ledgers")).json()["compare"]
    assert compare["start"] is None and "no history yet" in compare["notes"][0]
    assert [(line["name"], line["points"], line["return"]) for line in compare["lines"]] == [
        ("strategy", [], None), ("ai", [], None), ("manual", [], None), ("buy_and_hold", [], None)]


async def test_the_comparison_charts_every_line_from_the_ai_start(ctx, priced, yahoo, clock):
    ctx.config = replace(ctx.config, ai_account=replace(ctx.config.ai_account, enabled=True))
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    await run_job(priced, "/api/jobs/prices", symbol="AAPL")
    # The start day's close only exists once that session has closed.
    assert (await priced.get("/api/ledgers")).json()["compare"]["left_out"] == ["AAPL"]
    yahoo.series["AAPL"].bars.append(bar("2026-09-23", 150.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)
    await run_job(priced, "/api/jobs/prices", symbol="AAPL")
    compare = (await priced.get("/api/ledgers")).json()["compare"]
    assert compare["start"] == "2026-09-23" and compare["left_out"] == []
    lines = {line["name"]: line for line in compare["lines"]}
    assert lines["ai"]["points"] == [["2026-09-23", 0.0], ["2026-09-24", 0.0]] and lines["ai"]["return"] == 0.0
    assert lines["buy_and_hold"]["points"] == [["2026-09-23", 0.0]]
    assert lines["buy_and_hold"]["caption"].startswith("Buy and hold: equal amounts of the 1 listing")
    assert all({"max_fall", "trades", "costs"} <= set(line) for line in compare["lines"])


@pytest.fixture
def stalled(monkeypatch):
    """Research that runs until cancelled, so queued jobs stay queued."""
    from oracle.services import activity_service

    async def research(c, symbol, **kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(activity_service, "research", research)


async def cancel_all(client):
    for job in (await client.get("/api/activity")).json()["jobs"]:
        await client.post(f"/api/activity/{job['id']}/cancel")


async def test_research_all_stale_queues_each_due_listing_once_and_says_what_is_waiting(loaded, stalled):
    for s in ("AAPL", "KO"):
        await loaded.post("/api/watchlists/main/items", json={"symbol": s})
    assert (await loaded.get("/api/ledgers/ai/stale")).json() == {"due": ["AAPL", "KO"], "waiting": []}
    pressed = await loaded.post("/api/ledgers/ai/research-stale")
    assert pressed.status_code == 202
    body = pressed.json()
    assert [j["subject"] for j in body["queued"]] == ["AAPL", "KO"] and body["waiting"] == []
    assert body["refused"] == []
    again = (await loaded.post("/api/ledgers/ai/research-stale")).json()
    assert (again["queued"], again["waiting"]) == ([], ["AAPL", "KO"])
    assert (await loaded.get("/api/ledgers/ai/stale")).json() == {"due": ["AAPL", "KO"], "waiting": ["AAPL", "KO"]}
    assert [j["label"] for j in (await loaded.get("/api/activity")).json()["jobs"]] == [
        "Research AAPL", "Research KO"]
    await cancel_all(loaded)


async def test_a_decision_line_research_button_pressed_twice_queues_one_job(loaded, stalled):
    await loaded.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    first = (await loaded.post("/api/jobs/research", params={"symbol": "AAPL"})).json()
    second = (await loaded.post("/api/jobs/research", params={"symbol": "AAPL"})).json()
    assert first["id"] == second["id"]
    assert len((await loaded.get("/api/activity")).json()["jobs"]) == 1
    await cancel_all(loaded)


async def test_nothing_watched_is_nothing_stale(loaded):
    assert (await loaded.get("/api/ledgers/ai/stale")).json() == {"due": [], "waiting": []}
    body = (await loaded.post("/api/ledgers/ai/research-stale")).json()
    assert (body["queued"], body["waiting"], body["refused"]) == ([], [], [])
