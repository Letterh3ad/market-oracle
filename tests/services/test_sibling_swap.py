from datetime import date, datetime, timezone

import pytest

from oracle.broker.orders import Side
from oracle.config import Config
from oracle.services.company_service import company_view
from oracle.services.context import open_context
from oracle.services.errors import InvalidSwap, UnknownSymbol
from oracle.services.universe_service import live_siblings, refresh_universe
from oracle.services.watch_service import show_watchlist, swap, watch
from oracle.store.health import HealthRepo
from oracle.store.paper import FillRow, PaperRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import APPLE, BASIC, FakeFinanceDatabase, equity

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def ctx(tmp_path):
    rows = BASIC + [equity("AAPL.NE", "Apple Inc.", exchange="NEO", isin=APPLE, currency="CAD"),
                    equity("AAPL.VI", "Apple Inc.", exchange="VIE", isin=APPLE, delisted="True")]
    fdb = FakeFinanceDatabase(rows)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), fdb.transport) as c:
        await refresh_universe(c)
        yield c


def kill(ctx, symbol):
    HealthRepo(ctx.conn).note_no_data(symbol, NOW.date(), 1)


def symbols(ctx, name="main"):
    return [i.symbol for i in show_watchlist(ctx, name)]


def test_siblings_are_the_companys_other_live_listings_best_first(ctx):
    kill(ctx, "AAPL")
    kill(ctx, "AAPL.MI")
    assert [s.symbol for s in live_siblings(ctx, "aapl")] == ["APC.DE", "AAPL.NE"]


def test_delisted_listings_are_never_siblings_and_a_lone_listing_has_none(ctx):
    assert [s.symbol for s in live_siblings(ctx, "TM")] == ["7203.T"]
    assert "AAPL.VI" not in [s.symbol for s in live_siblings(ctx, "AAPL")]
    assert live_siblings(ctx, "OLD.F") == []


def test_swap_replaces_the_symbol_in_place_on_every_list(ctx):
    for s in ("TM", "AAPL", "7203.T"):
        watch(ctx, s)
    watch(ctx, "AAPL", "tech")
    watch(ctx, "APC.DE", "euro")
    kill(ctx, "AAPL")
    result = swap(ctx, "aapl", "apc.de")
    assert (result.old, result.new, result.watchlists) == ("AAPL", "APC.DE", ("main", "tech"))
    assert symbols(ctx) == ["TM", "APC.DE", "7203.T"]
    assert symbols(ctx, "tech") == ["APC.DE"]
    assert symbols(ctx, "euro") == ["APC.DE"]


def test_swap_onto_a_list_already_holding_the_new_symbol_drops_the_old(ctx):
    for s in ("AAPL", "TM", "APC.DE"):
        watch(ctx, s)
    swap(ctx, "AAPL", "APC.DE")
    assert symbols(ctx) == ["TM", "APC.DE"]


def test_swap_refuses_another_companys_listing(ctx):
    watch(ctx, "AAPL")
    with pytest.raises(InvalidSwap, match="not a listing of Apple"):
        swap(ctx, "AAPL", "TM")
    assert symbols(ctx) == ["AAPL"]


def test_swap_refuses_a_dead_or_delisted_target(ctx):
    watch(ctx, "AAPL")
    kill(ctx, "APC.DE")
    with pytest.raises(InvalidSwap, match="no price source has had data for APC.DE"):
        swap(ctx, "AAPL", "APC.DE")
    with pytest.raises(InvalidSwap, match="AAPL.VI is delisted"):
        swap(ctx, "AAPL", "AAPL.VI")
    with pytest.raises(InvalidSwap, match="same listing"):
        swap(ctx, "AAPL", "aapl")
    with pytest.raises(UnknownSymbol):
        swap(ctx, "AAPL", "NOPE")


def test_swap_refuses_an_unwatched_listing(ctx):
    with pytest.raises(InvalidSwap, match="AAPL is on no watchlist"):
        swap(ctx, "AAPL", "APC.DE")


def test_paper_positions_stay_on_the_old_symbol_and_are_named(ctx):
    watch(ctx, "AAPL")
    repo = PaperRepo(ctx.conn)
    order = repo.add_order("manual", "AAPL", Side.BUY, 100.0, None, "t", date(2026, 9, 18), "manual")
    repo.record_fill(FillRow(order.id, "manual", "AAPL", Side.BUY, date(2026, 9, 18), 1, 100.0, "USD",
                             1.0, -100.0, 0.0, 0.0, 0.0, 0.0))
    result = swap(ctx, "AAPL", "APC.DE")
    assert result.held_by == ("manual",)
    assert [f.symbol for f in repo.fills("manual")] == ["AAPL"]


async def test_the_watchlist_and_company_view_show_a_dead_listings_siblings(ctx):
    watch(ctx, "AAPL")
    watch(ctx, "TM")
    kill(ctx, "AAPL")
    aapl, tm = show_watchlist(ctx)
    assert aapl.dead_since == NOW.date()
    assert [s.symbol for s in aapl.siblings] == ["APC.DE", "AAPL.MI", "AAPL.NE"]
    assert tm.dead_since is None and tm.siblings == ()
    view = await company_view(ctx, "AAPL", refresh=False)
    assert [s.symbol for s in view.siblings] == ["APC.DE", "AAPL.MI", "AAPL.NE"]
    assert view.watchlists == ("main",)
