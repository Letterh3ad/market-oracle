"""Discovered listings on the paper accounts: the AI account trades them behind a switch, the
strategy account and buy-and-hold keep to the user's own picks."""

from dataclasses import replace
from datetime import date

import pytest

from oracle.services.paper_service import (
    DISCOVERED_OFF, account_view, ai_step, comparison, decision_log, fill_pending, strategy_step, traded_by,
)
from oracle.services.watch_service import watch
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.store.watchlists import WatchlistRepo
from tests.services.test_paper import ai_on, clock, ctx, next_day, seed, verdict, yahoo  # noqa: F401


def found(ctx, symbol):
    WatchlistRepo(ctx.conn).add(DISCOVERED_LIST, symbol, "2026-09-21T20:00:00Z")


def ai_trades(ctx, on):
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, ai_trades=on))


async def test_the_strategy_account_leaves_discovered_listings_alone(ctx):
    watch(ctx, "AAPL")
    found(ctx, "VOD.L")
    for s in ("AAPL", "VOD.L"):
        seed(ctx, s, "buy")
    decisions = {d.symbol: d for d in strategy_step(ctx)}
    assert set(decisions) == {"AAPL"}
    assert {d.symbol for d in decision_log(ctx, "strategy")} == {"AAPL"}
    assert traded_by(ctx, "strategy") == ["AAPL"]


async def test_a_listing_on_both_lists_is_the_users_own_pick(ctx):
    watch(ctx, "VOD.L")
    found(ctx, "VOD.L")
    assert traded_by(ctx, "strategy") == ["VOD.L"]


async def test_the_ai_account_trades_discovered_verdicts_while_the_switch_is_on(ctx):
    watch(ctx, "AAPL")
    found(ctx, "VOD.L")
    seed(ctx, "VOD.L", "hold")  # a risk size to buy with
    ai_on(ctx)
    verdict(ctx, "VOD.L", "buy", 0.9)
    decisions = {d.symbol: d for d in ai_step(ctx)}
    assert decisions["VOD.L"].action == "buy"
    assert traded_by(ctx, "ai") == ["AAPL", "VOD.L"]


async def test_off_the_ai_account_skips_discovered_listings_saying_why(ctx):
    watch(ctx, "AAPL")
    found(ctx, "VOD.L")
    seed(ctx, "VOD.L", "hold")
    ai_on(ctx)
    ai_trades(ctx, False)
    verdict(ctx, "VOD.L", "buy", 0.9)
    decisions = {d.symbol: d for d in ai_step(ctx)}
    assert (decisions["VOD.L"].action, decisions["VOD.L"].reason) == ("skip", DISCOVERED_OFF)
    assert "Settings" in DISCOVERED_OFF
    assert account_view(ctx, "ai").pending == ()


async def test_a_held_discovered_listing_can_still_be_sold_with_trading_off(ctx, yahoo, clock):
    found(ctx, "AAPL")
    seed(ctx, "AAPL", "hold")
    ai_on(ctx)
    verdict(ctx, "AAPL", "buy", 0.9)
    ai_step(ctx)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    ai_trades(ctx, False)
    verdict(ctx, "AAPL", "sell", 0.9, day=date(2026, 9, 22))
    [d] = ai_step(ctx)
    assert d.action == "sell", "a position is never stranded by the switch"


async def test_buy_and_hold_measures_the_users_own_picks_only(ctx):
    watch(ctx, "AAPL")
    found(ctx, "VOD.L")
    ai_on(ctx)
    fill_pending(ctx)
    c = comparison(ctx)
    hold = next(line for line in c.lines if line.name == "buy_and_hold")
    assert "1 listing " in hold.caption and c.left_out == ()
    assert any("VOD.L" in n and "discovered" in n for n in c.notes)


@pytest.mark.parametrize("account", ["strategy", "ai"])
async def test_a_listing_only_discovered_is_not_counted_as_the_users(ctx, account):
    found(ctx, "VOD.L")
    assert ("VOD.L" in traded_by(ctx, account)) == (account == "ai")
