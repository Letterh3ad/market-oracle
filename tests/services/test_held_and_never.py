"""A held position is never stranded when its listing leaves every list, and the never list
holds wherever a listing could be watched or bought."""

from dataclasses import replace
from datetime import date

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import NeverListed
from oracle.services.fx_service import update_fx
from oracle.services.paper_service import (
    account_view, ai_step, fill_pending, place_buy, strategy_step, traded_by,
)
from oracle.services.prices_service import update_listing, update_watched
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import swap, unwatch, watch
from oracle.store.bars import BarRepo
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.store.watchlists import WatchlistRepo
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import bar
from tests.services.test_paper import ai_on, clock, next_day, ohlc, seed, verdict, yahoo  # noqa: F401

UNIVERSE = [
    equity("AAPL", "Apple Inc.", currency="USD"),
    equity("VOD.L", "Vodafone", "United Kingdom", "LSE", currency="GBP"),
    equity("TSLA", "Tesla, Inc.", isin="US88160R1014", currency="USD"),
    # Only another line of the company carries the never-listed name.
    equity("XYZM", "XYZ Motors Inc", isin="US0000000001", currency="USD"),
    equity("XYZ.DE", "Tesla XYZ AG", "Germany", "GER", isin="US0000000001", currency="EUR"),
]


@pytest.fixture
async def ctx(tmp_path, yahoo, clock):
    yahoo.add("TSLA", "USD", "America/New_York", [bar("2026-09-18", 200.0), bar("2026-09-21", 200.0)])
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo)
    with open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport) as c:
        await refresh_universe(c)
        for s in ("AAPL", "VOD.L", "TSLA"):
            await update_listing(c, s)
        await update_fx(c)
        yield c


def never(ctx, *names):
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, never=names))


async def hold_aapl(ctx, yahoo, clock, account):
    """AAPL bought and filled on `account`."""
    step = strategy_step if account == "strategy" else ai_step
    seed(ctx, "AAPL", "buy")
    if account == "ai":
        ai_on(ctx)
        verdict(ctx, "AAPL", "buy", 0.9)
    step(ctx)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    assert [p.symbol for p in account_view(ctx, account).positions] == ["AAPL"]


async def crash_aapl(yahoo, clock):
    """A day AAPL halves, stored only if something still prices it."""
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-23", 50.0, 50.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)


@pytest.mark.parametrize(("account", "listed_on"), [("strategy", "main"), ("ai", "main"), ("ai", DISCOVERED_LIST)])
async def test_a_held_listing_taken_off_every_list_is_still_priced_and_stopped_out(
        ctx, yahoo, clock, account, listed_on):
    watch(ctx, "AAPL", listed_on)
    await hold_aapl(ctx, yahoo, clock, account)
    assert unwatch(ctx, "AAPL", listed_on)
    assert "AAPL" in traded_by(ctx, account)
    await crash_aapl(yahoo, clock)
    await update_watched(ctx)
    assert BarRepo(ctx.conn).last_date("AAPL") == date(2026, 9, 23), "a held listing keeps its prices"
    step = strategy_step if account == "strategy" else ai_step
    [d] = [d for d in step(ctx) if d.symbol == "AAPL"]
    assert d.action == "sell" and d.reason.startswith("stop")


async def test_a_held_listing_off_every_list_is_never_bought_again(ctx, yahoo, clock):
    watch(ctx, "AAPL")
    await hold_aapl(ctx, yahoo, clock, "strategy")
    unwatch(ctx, "AAPL")
    seed(ctx, "AAPL", "strong buy", probability=0.95, as_of=date(2026, 9, 22))
    assert not [d for d in strategy_step(ctx) if d.action == "buy"]
    seed(ctx, "AAPL", "strong sell", as_of=date(2026, 9, 22))
    [d] = strategy_step(ctx)
    assert d.action == "sell", "a sell lean still applies"
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-23", 100.0, 100.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)
    await update_watched(ctx)
    fill_pending(ctx)
    assert account_view(ctx, "strategy").positions == ()
    seed(ctx, "AAPL", "strong buy", probability=0.95, as_of=date(2026, 9, 23))
    assert "AAPL" not in traded_by(ctx, "strategy") and strategy_step(ctx) == []


async def test_the_manual_accounts_holdings_are_priced_too(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    await crash_aapl(yahoo, clock)
    update = await update_watched(ctx)
    assert "AAPL" in {o.symbol for o in update.listings}
    assert BarRepo(ctx.conn).last_date("AAPL") == date(2026, 9, 23)


@pytest.mark.parametrize("watchlist", ["main", DISCOVERED_LIST, "cars"])
@pytest.mark.parametrize("symbol", ["TSLA", "XYZM"])
async def test_tesla_is_never_watched_on_any_list(ctx, watchlist, symbol):
    never(ctx, "Nikola")
    with pytest.raises(NeverListed, match="never watched or traded"):
        watch(ctx, symbol, watchlist)
    assert WatchlistRepo(ctx.conn).all_symbols() == []


async def test_the_users_never_list_keeps_a_company_off_the_discovered_list_only(ctx):
    never(ctx, "Vodafone")
    with pytest.raises(NeverListed, match="never list"):
        watch(ctx, "VOD.L", DISCOVERED_LIST)
    assert watch(ctx, "VOD.L").added, "the user's own pick is theirs to make"


async def test_a_never_listed_discovered_listing_is_not_bought_but_can_be_sold(ctx, yahoo, clock):
    WatchlistRepo(ctx.conn).add(DISCOVERED_LIST, "VOD.L", "2026-09-21T20:00:00Z")  # found before the setting
    seed(ctx, "VOD.L", "hold")
    ai_on(ctx)
    never(ctx, "Vodafone")
    verdict(ctx, "VOD.L", "buy", 0.9)
    [d] = ai_step(ctx)
    assert (d.action, "never" in d.reason) == ("skip", True)
    assert account_view(ctx, "ai").pending == ()


async def test_a_held_never_listed_discovered_listing_can_still_be_sold(ctx, yahoo, clock):
    watch(ctx, "AAPL", DISCOVERED_LIST)
    await hold_aapl(ctx, yahoo, clock, "ai")
    never(ctx, "Apple")
    verdict(ctx, "AAPL", "sell", 0.9, day=date(2026, 9, 22))
    [d] = ai_step(ctx)
    assert d.action == "sell"


@pytest.mark.parametrize("account", ["strategy", "ai"])
async def test_neither_account_buys_tesla_whatever_list_it_is_on(ctx, account):
    WatchlistRepo(ctx.conn).add("main", "TSLA", "2026-09-21T20:00:00Z")  # watched before the rule
    never(ctx)
    seed(ctx, "TSLA", "strong buy", probability=0.95)
    ai_on(ctx)
    verdict(ctx, "TSLA", "strong buy", 0.95)
    [d] = (strategy_step if account == "strategy" else ai_step)(ctx)
    assert (d.symbol, d.action, "never" in d.reason) == ("TSLA", "skip", True)
    assert account_view(ctx, account).pending == ()


async def test_swap_refuses_a_never_listed_target(ctx):
    WatchlistRepo(ctx.conn).add("main", "XYZM", "2026-09-21T20:00:00Z")  # watched before the never name showed up
    with pytest.raises(NeverListed, match="never watched or traded"):
        swap(ctx, "XYZM", "XYZ.DE")
    assert WatchlistRepo(ctx.conn).all_symbols() == ["XYZM"]


async def test_a_pending_buy_for_a_now_never_listed_company_is_rejected_at_fill(ctx, yahoo, clock):
    watch(ctx, "AAPL", DISCOVERED_LIST)
    place_buy(ctx, "AAPL", 1000.0, "ai", source="ai")  # placed before the discovery never rule
    never(ctx, "Apple")
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-22", 100.0, 100.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-22", 1.25))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    [outcome] = fill_pending(ctx)
    assert (outcome.status.value, outcome.note) == ("rejected", "on the never list")
    assert account_view(ctx, "ai").positions == ()


async def test_no_account_places_a_tesla_buy(ctx):
    for account in ("manual", "strategy", "ai"):
        with pytest.raises(NeverListed):
            place_buy(ctx, "TSLA", 1000.0, account)
