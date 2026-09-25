import json
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, DiscoveryConfig
from oracle.services.context import open_context
from oracle.services.errors import JobAlreadyRunning
from oracle.services.jobs_service import single_flight
from oracle.services.paper_service import account_view, decision_log, place_buy
from oracle.services.replay_service import study_status
from oracle.services.tick_service import STAGES, last_tick, run_tick
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.fx import FxRepo
from oracle.store.replay import ReplayRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)  # Monday evening, after the US close


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar("2026-09-17", 100.0), bar("2026-09-18", 100.0)])
    y.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-17", 1.25), bar("2026-09-18", 1.25)])
    return y


@pytest.fixture
async def ctx(tmp_path, yahoo):
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD"),
                                       equity("NOBARS", "No Bars", currency="USD"),
                                       equity("7203.T", "Toyota", "Japan", "JPX", currency="JPY")]), yahoo)
    # Discovery off: its news searches have no fake here, and it has its own tests.
    config = Config(db_path=tmp_path / "o.db", discovery=DiscoveryConfig(enabled=False))
    with open_context(config, None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        watch(c, "AAPL")
        yield c


def _states(run):
    return {s.name: s.state for s in run.stages}


async def test_a_tick_runs_every_stage_in_order(ctx):
    run = await run_tick(ctx)
    assert [s.name for s in run.stages] == list(STAGES) == [
        "prices", "fx", "fill", "analyse", "discover", "research", "step", "ai_step"]
    assert set(_states(run).values()) == {"ok"} and run.outcome == "ok"
    # The step ran on the analysis the same tick produced.
    assert [d.symbol for d in decision_log(ctx, "strategy")] == ["AAPL"]
    assert run.stages[-2].summary == "0 buy, 0 sell, 1 skip, 0 no change"


async def test_prices_come_before_the_fill_that_needs_them(ctx, yahoo):
    await run_tick(ctx)
    place_buy(ctx, "AAPL", 1000.0)
    yahoo.series["AAPL"].bars += [bar("2026-09-21", 100.0), bar("2026-09-22", 100.0)]
    yahoo.series["GBPUSD=X"].bars += [bar("2026-09-21", 1.25), bar("2026-09-22", 1.25)]
    ctx.clock.advance(86400 + 3600)
    run = await run_tick(ctx)
    assert "1 filled" in next(s.summary for s in run.stages if s.name == "fill")
    assert account_view(ctx, "manual").positions


async def test_a_listing_failing_makes_prices_partial_and_the_rest_still_run(ctx):
    watch(ctx, "NOBARS")
    run = await run_tick(ctx)
    states = _states(run)
    assert states["prices"] == "partial" and "NOBARS" in run.stages[0].error
    assert states["fill"] == states["step"] == "ok"
    assert run.outcome == "partial" and run.first_error.startswith("prices: ")


async def test_the_prices_stage_keeps_the_replay_study_priced_without_failing_for_it(ctx):
    listing = [{"symbol": "NOBARS", "name": "No Bars", "region": "US", "sector": "x", "market_cap": None}]
    ReplayRepo(ctx.conn).create(NOW, date(2026, 6, 1), date(2026, 6, 1), json.dumps(listing),
                                [("NOBARS", date(2026, 6, 1))], allowed=1)
    run = await run_tick(ctx)
    prices = run.stages[0]
    assert prices.state == "ok" and prices.summary.endswith("; replay study: 0 of 1 listings refetched, 1 failed")
    score = study_status(ctx).score
    assert score.prices_checked_at.date() == NOW.date() and score.price_failures == ("NOBARS",)


async def test_the_fx_stage_fetches_the_currencies_of_the_replay_studys_listings(ctx, yahoo):
    yahoo.add("7203.T", "JPY", "Asia/Tokyo", [bar("2026-06-01", 3000.0), bar("2026-09-18", 3100.0)])
    yahoo.add("GBPJPY=X", "JPY", "Europe/London", [bar("2026-06-01", 190.0), bar("2026-09-18", 195.0)])
    listing = [{"symbol": "7203.T", "name": "Toyota", "region": "Japan", "sector": "x", "market_cap": None}]
    ReplayRepo(ctx.conn).create(NOW, date(2026, 6, 1), date(2026, 6, 1), json.dumps(listing),
                                [("7203.T", date(2026, 6, 1))], allowed=1)
    run = await run_tick(ctx)
    fx = next(s for s in run.stages if s.name == "fx")
    assert fx.state == "ok" and fx.summary.startswith("2 of 2 currencies updated")
    assert FxRepo(ctx.conn, "GBP").history("JPY")


async def test_a_replay_study_listing_resting_after_repeated_failures_is_skipped_by_the_tick(ctx):
    listing = [{"symbol": "NOBARS", "name": "No Bars", "region": "US", "sector": "x", "market_cap": None}]
    ReplayRepo(ctx.conn).create(NOW, date(2026, 6, 1), date(2026, 6, 1), json.dumps(listing),
                                [("NOBARS", date(2026, 6, 1))], allowed=1)
    for _ in range(3):
        await run_tick(ctx)
    prices = (await run_tick(ctx)).stages[0]
    assert prices.summary.endswith("; replay study: 1 skipped")
    assert study_status(ctx).score.price_failures == ("NOBARS",)


async def test_a_replay_study_price_error_never_fails_or_hides_the_prices_stage(ctx, monkeypatch):
    from oracle.services import tick_service

    async def broken(c):
        raise RuntimeError("boom")

    monkeypatch.setattr(tick_service, "update_study_prices", broken)
    listing = [{"symbol": "NOBARS", "name": "No Bars", "region": "US", "sector": "x", "market_cap": None}]
    ReplayRepo(ctx.conn).create(NOW, date(2026, 6, 1), date(2026, 6, 1), json.dumps(listing),
                                [("NOBARS", date(2026, 6, 1))], allowed=1)
    prices = (await run_tick(ctx)).stages[0]
    assert prices.state == "ok" and prices.summary.startswith("1 of 1 listings updated")
    assert prices.summary.endswith("; replay study prices failed: boom")


async def test_a_stage_that_cannot_run_is_recorded_and_later_stages_still_run(ctx):
    with single_flight(ctx, "prices_update", 60.0):
        run = await run_tick(ctx)
    states = _states(run)
    assert states["prices"] == "failed" and "already running" in run.stages[0].error
    assert states["fx"] == states["fill"] == states["step"] == "ok"


async def test_every_stage_failing_is_a_failed_tick(ctx, monkeypatch):
    from oracle.services import tick_service

    async def boom(ctx, say):
        raise RuntimeError("broken")

    monkeypatch.setattr(tick_service, "STAGES", {name: boom for name in STAGES})
    run = await run_tick(ctx)
    assert run.outcome == "failed" and run.first_error == "prices: broken"


async def test_ticks_never_overlap(ctx):
    with single_flight(ctx, "tick", 60.0):
        with pytest.raises(JobAlreadyRunning, match="tick is already running"):
            await run_tick(ctx)


async def test_each_tick_is_stored_and_the_latest_one_read_back(ctx):
    assert last_tick(ctx) is None
    await run_tick(ctx)
    ctx.clock.advance(60)
    second = await run_tick(ctx)
    stored = last_tick(ctx)
    assert stored.id == second.id and stored.stages == second.stages
    assert (stored.trigger, stored.started_at) == ("manual", second.started_at)


async def test_a_tick_stopped_part_way_still_leaves_a_record(ctx, monkeypatch):
    import asyncio

    from oracle.services import tick_service

    async def stopped(ctx, say):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tick_service, "STAGES", {**STAGES, "analyse": stopped})
    with pytest.raises(asyncio.CancelledError):
        await run_tick(ctx)
    stored = last_tick(ctx)
    assert [s.name for s in stored.stages] == ["prices", "fx", "fill"]
    assert stored.outcome == "interrupted" and stored.note.startswith("interrupted")
