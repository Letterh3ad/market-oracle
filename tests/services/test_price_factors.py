from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.broker.orders import Side
from oracle.config import Config
from oracle.market.bars import Bar
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, UnknownWatchlist
from oracle.services.factors_service import company_factors, factor_set, factor_table
from oracle.store.bars import BarRepo
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.store.paper import FillRow, PaperRepo
from oracle.store.watchlists import WatchlistRepo
from tests.analysts.bars import weekdays
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
FIRST = TODAY - timedelta(days=420)


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        yield c


def store(ctx, symbol, value, first=FIRST, last=TODAY):
    bars = [Bar(symbol, d, value(d), None, None, value(d), value(d), 1, "USD", "test")
            for d in weekdays(700, first) if d <= last]
    BarRepo(ctx.conn).upsert(bars, "2026-09-25T20:00:00Z")


def rising(rate):
    return lambda d: 100.0 * (1 + rate) ** (d - FIRST).days * (1.01 if d.toordinal() % 2 else 1.0)


def hold(ctx, symbol):
    repo = PaperRepo(ctx.conn)
    order = repo.add_order("manual", symbol, Side.BUY, 100.0, None, "2026-09-01T20:00:00Z", TODAY, "manual")
    repo.record_fill(FillRow(order.id, "manual", symbol, Side.BUY, TODAY, 1, 100.0, "USD", 1.0, -100.0, 0, 0, 0, 0))


@pytest.fixture
def listed(ctx):
    repo = WatchlistRepo(ctx.conn)
    for n, symbol in enumerate(["UP", "MID", "DOWN"]):
        store(ctx, symbol, rising(0.001 * (1 - n)))
        repo.add("main", symbol, "2026-09-01T00:00:00Z")
    store(ctx, "FOUND", rising(0.0005))
    repo.add(DISCOVERED_LIST, "FOUND", "2026-09-01T00:00:00Z")
    store(ctx, "HELD", rising(-0.0005))
    hold(ctx, "HELD")
    store(ctx, "YOUNG", rising(0.0), first=TODAY - timedelta(days=150))
    repo.add("main", "YOUNG", "2026-09-01T00:00:00Z")
    return ctx


def test_the_set_is_the_watched_discovered_and_held_listings(listed):
    assert factor_set(listed) == ["DOWN", "FOUND", "HELD", "MID", "UP", "YOUNG"]


def test_the_table_ranks_across_the_whole_set_strongest_first(listed):
    table = factor_table(listed)
    assert table.as_of == TODAY and table.set_size == 6 and table.ranked == 5
    assert [r.symbol for r in table.rows] == ["UP", "FOUND", "MID", "HELD", "DOWN", "YOUNG"]
    top, young = table.rows[0], table.rows[-1]
    assert top.momentum_pct == 1.0 and top.momentum > 0.3
    assert young.momentum_pct is None and "year" in young.note and young.low_vol_pct is not None


def test_a_list_filter_shows_its_listings_ranked_against_the_whole_set(listed):
    table = factor_table(listed, watchlist="main")
    assert [r.symbol for r in table.rows] == ["UP", "MID", "DOWN", "YOUNG"]
    assert table.rows[1].momentum_pct == 0.5 and table.set_size == 6
    with pytest.raises(UnknownWatchlist):
        factor_table(listed, watchlist="nope")


def test_an_earlier_day_never_reads_later_bars(listed):
    day = TODAY - timedelta(days=60)
    before = factor_table(listed, as_of=day)
    # A crash after the day, and a new listing priced only after it.
    store(listed, "UP", lambda d: 1.0 if d > day else rising(0.001)(d))
    assert factor_table(listed, as_of=day) == before
    assert all(r.as_of is None or r.as_of <= day for r in before.rows)


def test_a_day_ahead_is_clamped_to_today_for_time_zones_ahead_of_utc(listed):
    assert factor_table(listed, as_of=TODAY + timedelta(days=1)).as_of == TODAY


def test_a_later_day_is_refused(listed):
    with pytest.raises(InvalidQuery):
        factor_table(listed, as_of=TODAY + timedelta(days=2))


def test_a_zero_close_leaves_one_listing_unranked_and_the_table_served(listed):
    BarRepo(listed.conn).upsert([Bar("MID", TODAY - timedelta(days=3), 0.0, None, None, 0.0, 0.0, 1, "USD", "test")],
                                "2026-09-25T20:00:00Z")
    table = factor_table(listed)
    mid = next(r for r in table.rows if r.symbol == "MID")
    assert mid.momentum_pct is None and "break" in mid.note and table.ranked == 4


def test_a_company_outside_the_set_is_ranked_against_it_and_says_so(listed):
    store(listed, "OTHER", rising(0.002))
    row = company_factors(listed, "OTHER")
    assert row.symbol == "OTHER" and row.momentum_pct == 1.0 and not row.in_set
    assert company_factors(listed, "MID").in_set


def test_nothing_watched_is_an_empty_table(ctx):
    table = factor_table(ctx)
    assert table.rows == () and table.set_size == 0 and table.as_of == date(2026, 9, 25)
