from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import JobAlreadyRunning
from oracle.services.jobs_service import single_flight
from oracle.services.paper_service import account_view, place_buy
from oracle.services.routine_service import update_listing_prices, update_prices
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
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
                                       equity("NOBARS", "No Bars", currency="USD")]), yahoo)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


async def test_one_listing_brings_its_prices_and_its_currency_rates(ctx):
    run = await update_listing_prices(ctx, "aapl")
    [listing] = run.update.listings
    assert (listing.symbol, listing.added, listing.error) == ("AAPL", 2, None)
    assert [(f.currency, f.added) for f in run.update.fx] == [("USD", 2)]


async def test_a_listing_with_no_prices_is_reported_not_raised(ctx):
    [listing] = (await update_listing_prices(ctx, "NOBARS")).update.listings
    assert listing.added == 0 and "NOBARS" in listing.error


async def test_one_listing_waits_its_turn_behind_a_watchlist_run(ctx):
    with single_flight(ctx, "prices_update", 60.0):
        with pytest.raises(JobAlreadyRunning):
            await update_listing_prices(ctx, "AAPL")


async def test_new_bars_fill_waiting_orders_on_the_way_in(ctx, yahoo):
    watch(ctx, "AAPL")
    await update_prices(ctx)
    place_buy(ctx, "AAPL", 1000.0)
    # Placed on the 21st, so it fills at the 22nd's open once that session has closed.
    yahoo.series["AAPL"].bars += [bar("2026-09-21", 100.0), bar("2026-09-22", 100.0)]
    yahoo.series["GBPUSD=X"].bars += [bar("2026-09-21", 1.25), bar("2026-09-22", 1.25)]
    ctx.clock.advance(86400 + 3600)
    run = await update_prices(ctx)
    assert [f.status for f in run.fills] == ["filled"] and account_view(ctx, "manual").positions
