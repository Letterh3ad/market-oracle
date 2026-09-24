from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import InvalidWatchlistName, UnknownSymbol, UnknownWatchlist
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import create_watchlist, show_watchlist, unwatch, watch, watchlists
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase, bz2_csv


@pytest.fixture
def fdb():
    return FakeFinanceDatabase(UNIVERSE)


@pytest.fixture
async def ctx(tmp_path, fdb):
    start = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(start), fdb.transport) as c:
        await refresh_universe(c)
        yield c


def symbols(ctx, name="main"):
    return [i.symbol for i in show_watchlist(ctx, name)]


def lists(ctx):
    return [(w.name, w.items, w.missing) for w in watchlists(ctx)]


def test_a_watched_listing_shows_with_its_details(ctx):
    w = watch(ctx, "apc.de")
    assert (w.symbol, w.name, w.watchlist, w.added) == ("APC.DE", "Apple Inc.", "main", True)
    [item] = show_watchlist(ctx)
    assert (item.symbol, item.name, item.exchange, item.currency, item.country, item.missing) == (
        "APC.DE", "Apple Inc.", "GER", "EUR", "United States", False
    )


def test_watching_twice_is_a_no_op(ctx):
    watch(ctx, "AAPL")
    assert not watch(ctx, "aapl").added
    assert symbols(ctx) == ["AAPL"]


def test_watching_one_listing_does_not_watch_the_others(ctx):
    watch(ctx, "APC.DE")
    assert symbols(ctx) == ["APC.DE"]


def test_items_keep_the_order_they_were_added(ctx):
    for s in ("KO", "AAPL", "7203.T"):
        watch(ctx, s)
    assert symbols(ctx) == ["KO", "AAPL", "7203.T"]


def test_lists_are_created_on_first_add_and_named_without_case(ctx):
    assert lists(ctx) == []
    watch(ctx, "AAPL")
    watch(ctx, "KO", "Dividends")
    watch(ctx, "T", "dividends")
    assert lists(ctx) == [("Dividends", 2, 0), ("main", 1, 0)]
    assert symbols(ctx, "DIVIDENDS") == ["KO", "T"]


def test_removing_the_last_member_keeps_the_list(ctx):
    watch(ctx, "KO", "dividends")
    assert unwatch(ctx, "ko", "dividends")
    assert lists(ctx) == [("dividends", 0, 0)]
    assert not unwatch(ctx, "KO", "dividends")


def test_an_empty_main_list_shows_empty(ctx):
    assert show_watchlist(ctx) == []


def test_an_unknown_list_suggests_a_real_one(ctx):
    watch(ctx, "KO", "dividends")
    with pytest.raises(UnknownWatchlist, match="dividends"):
        show_watchlist(ctx, "dividend")


async def test_a_listing_gone_after_a_refresh_stays_flagged_missing(ctx, fdb):
    watch(ctx, "KO")
    watch(ctx, "AAPL")
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([r for r in UNIVERSE if r["symbol"] != "KO"])
    await refresh_universe(ctx)
    assert [(i.symbol, i.missing) for i in show_watchlist(ctx)] == [("KO", True), ("AAPL", False)]
    assert lists(ctx) == [("main", 2, 1)]
    assert unwatch(ctx, "KO")


def test_an_unknown_symbol_is_refused_with_suggestions(ctx):
    with pytest.raises(UnknownSymbol, match="AAPL"):
        watch(ctx, "APPL")


@pytest.mark.parametrize("bad", ["", "   ", "x" * 41])
def test_a_bad_list_name_is_refused(ctx, bad):
    with pytest.raises(InvalidWatchlistName):
        watch(ctx, "AAPL", bad)


async def test_a_listing_delisted_after_a_refresh_is_marked(ctx, fdb):
    watch(ctx, "KO")
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv(
        [dict(r, delisted="True") if r["symbol"] == "KO" else r for r in UNIVERSE]
    )
    await refresh_universe(ctx)
    [item] = show_watchlist(ctx)
    assert (item.delisted, item.missing) == (True, False)


def test_a_list_created_empty_is_listed_and_shows_empty(ctx):
    assert create_watchlist(ctx, "  Dividend   picks ") == ("Dividend picks", True)
    assert lists(ctx) == [("Dividend picks", 0, 0)] and symbols(ctx, "dividend picks") == []


def test_creating_a_list_that_exists_keeps_it_as_named(ctx):
    watch(ctx, "AAPL", "Dividends")
    assert create_watchlist(ctx, "dividends") == ("Dividends", False)
    assert lists(ctx) == [("Dividends", 1, 0)]


@pytest.mark.parametrize("bad", ["", "   ", "x" * 41])
def test_a_list_with_a_bad_name_is_not_created(ctx, bad):
    with pytest.raises(InvalidWatchlistName):
        create_watchlist(ctx, bad)
    assert lists(ctx) == []
