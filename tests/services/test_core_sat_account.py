"""The live core-sat account: 75% an equal-weight core of the main list rebalanced yearly, 25% the
regime book's satellite, each sleeve with its own cash inside the one account."""

from dataclasses import replace
from datetime import date

import pytest

from oracle.broker.core_sat import EXPIRED, REBALANCE
from oracle.broker.orders import Action, Side, Status
from oracle.services.core_sat_service import (
    BUY, CORE_SOURCE, REBALANCE as REBALANCE_PHASE, SAT_SOURCE, START, core_phase, next_rebalance, sleeves,
)
from oracle.services.paper_service import (
    ACCOUNTS, LINE_NAMES, TRADERS, account_view, decision_log, fill_pending, place_buy, traded_by,
)
from oracle.services.regime_service import CORE_SAT_OFF, NOT_CORE, build_book, regime_card, regime_step
from oracle.services.watch_service import watch
from oracle.store.paper import Order, PaperRepo
from tests.fakes.regime import use_stub
from tests.services.test_paper import clock, ctx, next_day, seed, yahoo  # noqa: F401
from tests.services.test_paper_discovered import ai_trades, found
from tests.services.test_regime_account import PARTS

DAY = date(2026, 9, 21)
ACCOUNT = "core-sat"


def step(ctx):
    if ctx.clock.utcnow().date() == DAY:
        ctx.clock.advance(86400)
    return regime_step(ctx, ACCOUNT)


def order(day, side=Side.BUY, status=Status.FILLED):
    return Order(1, ACCOUNT, "AAPL", side, 1.0, None, day, status, None, CORE_SOURCE)


def test_core_sat_is_listed_on_by_default_and_its_cash_splits_75_25(ctx):
    assert ACCOUNT in ACCOUNTS and ACCOUNT in TRADERS and LINE_NAMES[ACCOUNT] == "Core-sat"
    assert ctx.config.core_sat_account.enabled is True
    core, sat = sleeves(ctx)
    assert (core.capital, sat.capital) == (7_500.0, 2_500.0)
    assert account_view(ctx, ACCOUNT).cash == 10_000.0
    assert (account_view(ctx, ACCOUNT, core).cash, account_view(ctx, ACCOUNT, sat).cash) == (7_500.0, 2_500.0)
    assert traded_by(ctx, ACCOUNT) == traded_by(ctx, "regime")


def test_the_core_starts_then_rebalances_yearly_and_buys_once_its_trims_filled():
    today = date(2027, 3, 1)
    assert core_phase([], today) == START
    assert core_phase([order(date(2026, 9, 22), status=Status.PENDING)], today) is None
    assert core_phase([order(date(2026, 9, 22))], today) is None
    assert core_phase([order(date(2026, 9, 22))], date(2027, 9, 22)) == REBALANCE_PHASE
    trims = [order(date(2026, 9, 22)), order(date(2027, 9, 22), Side.SELL)]
    assert core_phase(trims, date(2027, 9, 23)) == BUY
    assert core_phase(trims + [order(date(2027, 9, 23))], date(2027, 9, 24)) is None
    assert core_phase(trims, date(2028, 9, 22)) == REBALANCE_PHASE
    assert next_rebalance(trims, date(2027, 9, 23)) == date(2028, 9, 22)


def test_off_it_decides_nothing_and_says_so(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    watch(ctx, "AAPL")
    ctx.config = replace(ctx.config, core_sat_account=replace(ctx.config.core_sat_account, enabled=False))
    assert step(ctx) is None and built == [] and decision_log(ctx, ACCOUNT) == []
    card = regime_card(ctx, account=ACCOUNT)
    assert (card.enabled, card.note) == (False, CORE_SAT_OFF) and "[core_sat_account]" in CORE_SAT_OFF


def test_the_first_step_buys_the_core_equally_and_the_satellite_from_its_own_cash(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("strong buy", 0.95, PARTS), "VOD.L": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold", size_share=0.5)
    seed(ctx, "VOD.L", "hold", size_share=0.5)
    watch(ctx, "AAPL")
    watch(ctx, "VOD.L")
    run = step(ctx)
    assert built[0].account == ACCOUNT
    orders = account_view(ctx, ACCOUNT).pending
    core = [o for o in orders if o.source == CORE_SOURCE]
    sat = [o for o in orders if o.source == SAT_SOURCE]
    assert {o.symbol for o in core} == {"AAPL", "VOD.L"} and all(o.side == Side.BUY for o in core)
    assert [o.cash for o in core] == pytest.approx([3_750.0, 3_750.0])
    assert {o.symbol for o in sat} == {"AAPL", "VOD.L"}
    # The satellite's rules see its 2,500 alone: at most 10% of it each, its cash floor kept.
    assert all(o.cash <= 0.1 * 2_500.0 + 1e-6 for o in sat)
    assert [d.reason.startswith(REBALANCE) for d in run.decisions[:2]] == [True, True]
    logged = decision_log(ctx, ACCOUNT)
    assert {d.symbol for d in logged if d.reason.startswith(REBALANCE)} == {"AAPL", "VOD.L"}
    assert all(d.explanation for d in logged if not d.reason.startswith(REBALANCE) and d.action == Action.BUY)
    assert step(ctx).decisions[:0] == [] and len(account_view(ctx, ACCOUNT).pending) == len(orders), \
        "a second step places no core order while its buys wait"


async def test_the_satellite_never_spends_core_cash_and_sells_only_its_own_shares(ctx, yahoo, clock, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("strong buy", 0.95, PARTS)})
    seed(ctx, "AAPL", "hold", size_share=0.5)
    watch(ctx, "AAPL")
    core_sleeve, sat_sleeve = sleeves(ctx)
    regime_step(ctx, ACCOUNT)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    core, sat = account_view(ctx, ACCOUNT, core_sleeve), account_view(ctx, ACCOUNT, sat_sleeve)
    [core_aapl], [sat_aapl] = core.positions, sat.positions
    assert core.cash >= 0 and sat.cash >= 0.2 * 2_500.0 * 0.95
    assert core.cash + core.holdings == pytest.approx(7_500.0, rel=0.02)
    assert sat.cash + sat.holdings == pytest.approx(2_500.0, rel=0.02)
    use_stub(monkeypatch, leans={"AAPL": ("strong sell", 0.9, PARTS)})
    regime_step(ctx, ACCOUNT)
    [sell] = [o for o in account_view(ctx, ACCOUNT).pending if o.side == Side.SELL]
    assert (sell.source, sell.shares) == (SAT_SOURCE, sat_aapl.shares)
    assert not [o for o in PaperRepo(ctx.conn).pending(ACCOUNT) if o.source == CORE_SOURCE]


def test_the_satellite_trades_the_main_lists_companies_only_as_its_backtest_does(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS), "VOD.L": ("strong buy", 0.95, PARTS)})
    seed(ctx, "AAPL", "hold", size_share=0.5)
    seed(ctx, "VOD.L", "hold", size_share=0.5)
    watch(ctx, "AAPL")
    ai_trades(ctx, True)
    found(ctx, "VOD.L")
    assert "VOD.L" in traded_by(ctx, ACCOUNT)
    regime_step(ctx, ACCOUNT)
    assert {o.symbol for o in account_view(ctx, ACCOUNT).pending} == {"AAPL"}
    [skip] = [d for d in decision_log(ctx, ACCOUNT) if d.symbol == "VOD.L"]
    assert skip.reason == NOT_CORE


def test_a_core_order_waiting_past_its_limit_expires_and_the_core_buys_afresh(ctx, monkeypatch):
    use_stub(monkeypatch)
    watch(ctx, "AAPL")
    regime_step(ctx, ACCOUNT)
    [first] = PaperRepo(ctx.conn).orders(ACCOUNT, CORE_SOURCE)
    ctx.clock.advance(3 * 86400)
    regime_step(ctx, ACCOUNT)
    assert len(PaperRepo(ctx.conn).orders(ACCOUNT, CORE_SOURCE)) == 1, "still within its limit: it waits"
    ctx.clock.advance(6 * 86400)
    regime_step(ctx, ACCOUNT)
    old, new = PaperRepo(ctx.conn).orders(ACCOUNT, CORE_SOURCE)
    assert (old.id, old.status, old.note) == (first.id, Status.REJECTED, EXPIRED)
    assert (new.status, new.side, new.symbol) == (Status.PENDING, Side.BUY, "AAPL")


def test_a_manual_or_other_account_order_is_no_sleeves(ctx):
    place_buy(ctx, "AAPL", 100.0, ACCOUNT, source="manual")
    core, sat = sleeves(ctx)
    assert account_view(ctx, ACCOUNT, core).pending == () == account_view(ctx, ACCOUNT, sat).pending


def test_the_card_shows_each_sleeve_and_the_next_rebalance(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold", size_share=0.5)
    watch(ctx, "AAPL")
    step(ctx)
    card = regime_card(ctx, account=ACCOUNT)
    assert card.sleeves["core"]["capital"] == 7_500.0 and card.sleeves["satellite"]["capital"] == 2_500.0
    assert card.sleeves["core"]["pending"] == 1 and card.sleeves["core"]["listings"] == 1
    assert card.sleeves["core"]["next_rebalance"] == "2027-09-22"
    assert card.state["mood"] == "calm_up"


def test_the_real_book_it_builds_is_the_regime_one_in_its_own_name(ctx):
    book = build_book(ctx, {}, None, account=ACCOUNT)
    assert (book._settings.spread_unscored, book._settings.rotation, book._source) == (False, None, ACCOUNT)
