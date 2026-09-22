from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, UnknownSymbol
from oracle.services.fx_service import update_fx
from oracle.services.paper_service import account_score, account_view, equity_history, fill_pending, place_buy, place_sell
from oracle.services.prices_service import update_listing
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)  # Monday evening


def ohlc(day, open_, close):
    return (date.fromisoformat(day), open_, open_, open_, close, close, 1000)


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 100.0), bar("2026-09-21", 100.0)])
    y.add("VOD.L", "GBp", "Europe/London", [bar("2026-09-21", 70.0)], open_at=datetime(2026, 1, 1, 8).time())
    y.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-18", 1.25), bar("2026-09-21", 1.25)],
          open_at=datetime(2026, 1, 1, 0).time())
    return y


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
async def ctx(tmp_path, yahoo, clock):
    universe = [equity("AAPL", "Apple Inc.", currency="USD"),
                equity("VOD.L", "Vodafone", "United Kingdom", "LSE", currency="GBP")]
    web = FakeWeb(FakeFinanceDatabase(universe), yahoo)
    manager = open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport)
    c = manager.__enter__()
    await refresh_universe(c)
    for s in ("AAPL", "VOD.L"):
        await update_listing(c, s)
    await update_fx(c)
    yield c
    manager.__exit__(None, None, None)


async def next_day(ctx, yahoo, clock, aapl_open=100.0):
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-22", aapl_open, aapl_open))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-22", 1.25))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)


async def test_a_buy_waits_for_the_next_open_then_fills_by_hand(ctx, yahoo, clock):
    place_buy(ctx, "aapl", 1000.0)
    assert fill_pending(ctx) == [] and account_view(ctx, "manual").pending
    await next_day(ctx, yahoo, clock)
    [done] = fill_pending(ctx)
    assert done.status == "filled"
    view = account_view(ctx, "manual")
    [pos] = view.positions
    assert (pos.symbol, pos.shares) == ("AAPL", 12)
    assert view.cash == pytest.approx(10_000 - 1202.2 / 1.2475)
    assert not view.pending and account_view(ctx, "strategy").cash == 10_000


async def test_not_enough_cash_is_rejected_and_cash_is_untouched(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 20_000.0)
    await next_day(ctx, yahoo, clock)
    [done] = fill_pending(ctx)
    assert done.status == "rejected" and "cash" in done.note
    assert account_view(ctx, "manual").cash == 10_000


async def test_less_than_one_share_is_rejected(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 50.0)
    await next_day(ctx, yahoo, clock)
    [done] = fill_pending(ctx)
    assert done.status == "rejected" and "share" in done.note


async def test_no_exchange_rate_for_the_fill_day_keeps_it_pending_and_says_why(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-28", 100.0, 100.0))
    clock.advance(8 * 86400)
    await update_listing(ctx, "AAPL")
    [done] = fill_pending(ctx)
    assert done.status == "pending" and "oracle fx update" in done.note


async def test_unknown_symbols_are_refused(ctx):
    with pytest.raises(UnknownSymbol):
        place_buy(ctx, "APPL", 1000.0)


async def test_a_partial_sell_realises_profit_at_average_cost_by_hand(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    place_sell(ctx, "AAPL", 6)
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-23", 110.0, 110.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)
    [done] = fill_pending(ctx)
    assert done.status == "filled"
    view = account_view(ctx, "manual")
    # Proceeds: 6 * 109.89 = 659.34 USD - 1 fee = 658.34 * 0.998 / 1.25; cost: half of
    # the 12-share buy (1202.2 / 1.2475).
    proceeds, half_cost = 658.34 * 0.998 / 1.25, 1202.2 / 1.2475 / 2
    assert view.realised == pytest.approx(proceeds - half_cost)
    assert view.positions[0].shares == 6 and view.positions[0].cost == pytest.approx(half_cost)


async def test_selling_what_is_not_held_is_refused(ctx, yahoo, clock):
    with pytest.raises(InvalidQuery, match="not held"):
        place_sell(ctx, "AAPL", 1)
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    with pytest.raises(InvalidQuery, match="12"):
        place_sell(ctx, "AAPL", 13)
    place_sell(ctx, "AAPL", None)
    with pytest.raises(InvalidQuery, match="already"):
        place_sell(ctx, "AAPL", 1)


async def test_equity_costs_and_one_snapshot_per_day_by_hand(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    fill_pending(ctx)  # a rerun the same day replaces the snapshot
    score = account_score(ctx, "manual")
    spent = 1202.2 / 1.2475
    assert score.equity == pytest.approx(10_000 - spent + 12 * 100.0 / 1.25)
    # Fee 1 USD, slippage 12 * 0.10 USD, and the FX spread: together exactly the gap
    # between what was spent and a cost-free fill (12 * 100 / 1.25).
    assert score.costs == pytest.approx(spent - 12 * 100.0 / 1.25)
    assert score.unrealised == pytest.approx(12 * 100.0 / 1.25 - spent)
    assert [d for d, _ in equity_history(ctx, "manual")] == [clock.utcnow().date()]
    assert account_score(ctx, "strategy").equity == 10_000 and account_score(ctx, "strategy").max_fall == 0.0


async def test_the_largest_fall_in_equity(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 5000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    peak = account_score(ctx, "manual").equity
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-23", 50.0, 50.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)
    fill_pending(ctx)
    low = account_score(ctx, "manual").equity
    assert account_score(ctx, "manual").max_fall == pytest.approx(low / peak - 1)
