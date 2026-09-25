from dataclasses import replace
from datetime import datetime, timezone

import pytest

from oracle.broker.orders import Side
from oracle.config import Config
from oracle.services.company_service import company_view
from oracle.services.context import open_context
from oracle.services.errors import PriceUnavailable
from oracle.services.prices_service import listing_health, update_listing, update_watched
from oracle.services.routine_service import update_listing_prices
from oracle.services.universe_service import refresh_universe, search_universe
from oracle.services.watch_service import watch
from oracle.store.paper import FillRow, PaperRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import BASIC, FakeFinanceDatabase
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)
DAY = 86400


@pytest.fixture
def yahoo():
    """AAPL has no series, so Yahoo answers "no data" for it, as it does for BTQ.DE."""
    y = FakeYahoo()
    y.add("TM", "USD", "America/New_York", [bar("2026-09-17", 100.0), bar("2026-09-18", 100.0)])
    return y


@pytest.fixture
async def ctx(tmp_path, yahoo):
    web = FakeWeb(FakeFinanceDatabase(BASIC), yahoo)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        watch(c, "TM")
        # AAPL is neither held nor watched: it stands in for an ordinary dead listing.
        # Watched (TM) and held cases are covered separately below.
        c.config = replace(c.config, compare=replace(c.config.compare, benchmarks=("AAPL",)))
        yield c


async def _hold(ctx, symbol) -> None:
    repo = PaperRepo(ctx.conn)
    order = repo.add_order("manual", symbol, Side.BUY, 100.0, None, "t", NOW.date(), "manual")
    repo.record_fill(FillRow(order.id, "manual", symbol, Side.BUY, NOW.date(), 1, 100.0, "USD",
                             1.0, -100.0, 0.0, 0.0, 0.0, 0.0))


async def primary(ctx):
    return (await company_view(ctx, "AAPL", refresh=False)).profile.primary_symbol


async def no_data_days(ctx, days):
    for _ in range(days):
        await update_watched(ctx, fx=False)
        ctx.clock.advance(DAY)


async def test_three_no_data_results_on_three_days_mark_a_listing_dead(ctx):
    await no_data_days(ctx, 2)
    assert not listing_health(ctx, "AAPL").dead
    await no_data_days(ctx, 1)
    health = listing_health(ctx, "AAPL")
    assert health.dead and health.since == NOW.date()


async def test_repeats_on_one_day_count_once(ctx):
    for _ in range(5):
        await update_watched(ctx, fx=False)
    ctx.clock.advance(DAY)
    await no_data_days(ctx, 1)
    assert not listing_health(ctx, "AAPL").dead


async def test_throttling_and_network_errors_never_count(ctx, yahoo):
    yahoo.fail["AAPL"] = 429
    await no_data_days(ctx, 5)
    assert not listing_health(ctx, "AAPL").dead and listing_health(ctx, "AAPL").streak == 0


async def test_a_dead_listing_is_skipped_by_watched_updates_and_reported_apart(ctx):
    await no_data_days(ctx, 3)
    run = await update_watched(ctx, fx=False)
    aapl = next(o for o in run.listings if o.symbol == "AAPL")
    assert aapl.error is None and aapl.skipped == "no source has data since 2026-09-21"
    assert [o.symbol for o in run.failed] == []


async def test_bars_coming_back_clear_the_streak_and_the_mark(ctx, yahoo):
    await no_data_days(ctx, 3)
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-23", 100.0)])
    # Check again: one listing, never skipped.
    run = await update_listing_prices(ctx, "AAPL")
    assert run.update.listings[0].added == 1
    health = listing_health(ctx, "AAPL")
    assert not health.dead and health.streak == 0


async def test_check_again_with_still_no_data_keeps_it_dead(ctx):
    await no_data_days(ctx, 3)
    with pytest.raises(PriceUnavailable):
        await update_listing(ctx, "AAPL")
    assert listing_health(ctx, "AAPL").dead


async def test_a_dead_listing_is_greyed_in_search_and_never_the_primary(ctx):
    assert (await primary(ctx)) == "AAPL"
    await no_data_days(ctx, 3)
    [hit] = [h for h in search_universe(ctx, "AAPL", all_listings=True) if h.symbol == "AAPL"]
    assert hit.dead
    assert (await primary(ctx)) != "AAPL"
    await refresh_universe(ctx)  # a refresh keeps it that way
    assert (await primary(ctx)) != "AAPL" and listing_health(ctx, "AAPL").dead


async def test_a_watched_listing_that_starts_answering_empty_is_still_fetched_and_never_marked_dead(ctx, yahoo):
    await update_watched(ctx, fx=False)  # TM stores its bars
    yahoo.series["TM"].bars = []  # Yahoo keeps the symbol but serves no bars (BTQ.DE)
    await no_data_days(ctx, 3)
    run = await update_watched(ctx, fx=False)
    tm = next(o for o in run.listings if o.symbol == "TM")
    assert tm.skipped is None, "a watched line is never skipped for being dead"
    assert not listing_health(ctx, "TM").dead


async def test_a_held_listing_with_three_no_data_days_is_still_fetched_and_never_marked_dead(ctx):
    await _hold(ctx, "AAPL")
    await no_data_days(ctx, 3)
    run = await update_watched(ctx, fx=False)
    aapl = next(o for o in run.listings if o.symbol == "AAPL")
    assert aapl.skipped is None, "a held line is never skipped for being dead"
    assert not listing_health(ctx, "AAPL").dead
