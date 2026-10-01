"""The live index account: all its cash in ACWI once, then held; and the frozen lines step nothing."""

from datetime import date

import pytest

from oracle.broker.orders import Action, Side
from oracle.market.bars import Bar
from oracle.services.index_service import BUY_ALL, NO_BARS, index_step
from oracle.services.paper_service import (
    ACCOUNTS, FROZEN, TRADERS, account_view, comparison, decision_log, fill_pending, frozen_note, strategy_step,
)
from oracle.services.regime_service import regime_card, regime_step
from oracle.services.watch_service import watch
from oracle.store.bars import BarRepo
from oracle.store.paper import DecisionRepo, EquityRepo, LoggedDecision
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import Listing
from tests.fakes.regime import use_stub
from tests.services.test_paper import clock, ctx, next_day, seed, yahoo  # noqa: F401

DAY = date(2026, 9, 21)


def add_acwi(ctx, bars=True):
    UniverseRepo(ctx.conn).add(Listing("ACWI", "iShares MSCI ACWI ETF", "ETF", "United States", "NGM", currency="USD",
                                       company_key="acwi", is_primary=True))
    if bars:
        BarRepo(ctx.conn).upsert([Bar("ACWI", d, 100, 100, 100, 100, 100, 1, "USD", "t")
                                  for d in (date(2026, 9, 18), DAY)], "x")


def test_index_is_listed_first_and_trades_by_itself(ctx):
    assert ACCOUNTS[0] == "index" and "index" in TRADERS
    assert account_view(ctx, "index").cash == ctx.config.risk.account


async def test_it_buys_acwi_with_all_its_cash_once_then_holds(ctx, yahoo, clock):
    add_acwi(ctx)
    [d] = index_step(ctx)
    assert (d.symbol, d.action, d.reason) == ("ACWI", Action.BUY, BUY_ALL)
    [order] = account_view(ctx, "index").pending
    assert (order.side, order.cash, order.source) == (Side.BUY, ctx.config.risk.account, "index")
    assert index_step(ctx) == [], "nothing more while its buy waits"
    BarRepo(ctx.conn).upsert([Bar("ACWI", date(2026, 9, 22), 100, 100, 100, 100, 100, 1, "USD", "t")], "x")
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    [pos] = account_view(ctx, "index").positions
    assert pos.symbol == "ACWI" and pos.shares > 0
    assert index_step(ctx) == [], "held: never bought again or sold"
    assert [x.reason for x in decision_log(ctx, "index")] == [BUY_ALL]
    assert EquityRepo(ctx.conn).history("index"), "its equity is recorded like every live line's"


def test_without_acwi_prices_it_still_orders_and_says_the_update_fetches_them(ctx):
    add_acwi(ctx, bars=False)
    [d] = index_step(ctx)
    assert d.action == Action.BUY and d.reason == BUY_ALL + NO_BARS


def test_without_acwi_in_the_universe_it_skips_and_says_why(ctx):
    [d] = index_step(ctx)
    assert d.action == Action.SKIP and "refresh the universe" in d.reason
    assert account_view(ctx, "index").pending == ()


def test_the_comparison_shows_the_index_line(ctx):
    assert "index" in [line.name for line in comparison(ctx).lines]


@pytest.mark.frozen_lines
def test_strategy_regime_and_regime_v2_are_frozen_like_regime_ai(ctx, monkeypatch):
    assert FROZEN == {"strategy", "regime", "regime-ai", "regime-v2"}
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, {"trend": 0.1})})
    seed(ctx, "AAPL", "buy")
    watch(ctx, "AAPL")
    for account in ("regime", "regime-v2"):
        DecisionRepo(ctx.conn).log([LoggedDecision(account, DAY, "AAPL", Action.SKIP, None, "before the freeze", None)])
    ctx.clock.advance(86400)
    assert strategy_step(ctx) is None and decision_log(ctx, "strategy") == []
    for account in ("regime", "regime-v2"):
        assert regime_step(ctx, account) is None
        card = regime_card(ctx, account=account)
        assert card.frozen and not card.enabled and card.note == frozen_note(account)
        assert [d.reason for d in card.decisions] == ["before the freeze"]
    assert built == []
    fill_pending(ctx)
    assert all(EquityRepo(ctx.conn).history(a) == [] for a in ("strategy", "regime", "regime-v2", "regime-ai"))
    assert EquityRepo(ctx.conn).history("core-sat") and EquityRepo(ctx.conn).history("index")
    for account in ("strategy", "regime", "regime-v2"):
        assert account in ACCOUNTS and "Frozen" in next(x.caption for x in comparison(ctx).lines if x.name == account)


def test_the_index_button_waits_for_no_tick_and_never_orders_beside_one(ctx):
    from oracle.services.errors import JobAlreadyRunning
    from oracle.services.jobs_service import single_flight
    from oracle.services.tick_service import run_index_step

    add_acwi(ctx)
    with single_flight(ctx, "tick", 60.0):
        with pytest.raises(JobAlreadyRunning):
            run_index_step(ctx)
    assert account_view(ctx, "index").pending == ()
    assert [d.action for d in run_index_step(ctx)] == [Action.BUY]
