from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.analysts.signals import AnalystSignal
from oracle.broker.rules import Lean
from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, UnknownSymbol
from oracle.services.fx_service import update_fx
from oracle.services.paper_service import (
    account_score, account_view, decision_log, equity_history, fill_pending, place_buy, place_sell,
    ai_step, comparison, strategy_step,
)
from oracle.services.prices_service import update_listing
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.leans import LeanRepo
from oracle.store.paper import PaperRepo
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo
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
    assert done.status == "pending" and "update FX rates first" in done.note


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


def seed(ctx, symbol, label, probability=0.7, size_share=0.05, as_of=date(2026, 9, 21)):
    LeanRepo(ctx.conn).save([Lean(symbol, as_of, label, probability, "baseline")], "because", "2026-09-21T20:00:00Z")
    SignalRepo(ctx.conn).save([AnalystSignal("risk", symbol, as_of, 0.0, 1.0, "r", (("size_share", size_share),))],
                              "2026-09-21T20:00:00Z")


async def test_a_strategy_step_buys_on_a_lean_and_logs_every_decision(ctx, yahoo, clock):
    seed(ctx, "AAPL", "buy")
    seed(ctx, "VOD.L", "buy", probability=0.5)
    watch(ctx, "AAPL")
    watch(ctx, "VOD.L")
    decisions = {d.symbol: d for d in strategy_step(ctx)}
    assert decisions["AAPL"].action == "buy" and decisions["AAPL"].cash == pytest.approx(500.0)
    assert decisions["VOD.L"].action == "skip"
    [order] = account_view(ctx, "strategy").pending
    assert (order.symbol, order.source) == ("AAPL", "strategy")
    logged = {d.symbol: d for d in decision_log(ctx, "strategy")}
    assert logged["VOD.L"].reason.startswith("probability") and logged["AAPL"].action == "buy"


async def test_a_strategy_step_sells_on_a_sell_lean(ctx, yahoo, clock):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy")
    strategy_step(ctx)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    seed(ctx, "AAPL", "strong sell", as_of=date(2026, 9, 22))
    [d] = [d for d in strategy_step(ctx) if d.symbol == "AAPL"]
    assert d.action == "sell"
    assert account_view(ctx, "strategy").pending[0].side == "sell"


async def test_a_step_skips_a_watched_listing_without_a_lean(ctx):
    watch(ctx, "AAPL")
    [d] = strategy_step(ctx)
    assert (d.symbol, d.action) == ("AAPL", "skip") and "no lean yet" in d.reason
    assert [x.action for x in decision_log(ctx, "strategy")] == ["skip"]


async def test_a_lean_older_than_the_last_bar_is_never_traded(ctx, yahoo, clock):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "strong buy", probability=0.9)
    await next_day(ctx, yahoo, clock)
    [d] = strategy_step(ctx)
    assert (d.action, d.reason) == ("skip", "baseline lean is out of date: analyse it again")
    assert account_view(ctx, "strategy").pending == ()


async def test_cash_in_pending_buys_is_not_spent_twice(ctx):
    ctx.config = replace(ctx.config, risk=replace(ctx.config.risk, cash_floor=0.85))
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy", size_share=0.5)
    [d] = strategy_step(ctx)
    assert d.cash == pytest.approx(1000.0)
    watch(ctx, "VOD.L")
    seed(ctx, "VOD.L", "buy", size_share=0.5)
    [d] = [d for d in strategy_step(ctx) if d.symbol == "VOD.L"]
    assert d.cash == pytest.approx(500.0)


async def test_a_buy_of_nothing_or_less_is_refused(ctx):
    for amount in (0.0, -500.0, float("nan"), float("inf")):
        with pytest.raises(InvalidQuery, match="more than nothing"):
            place_buy(ctx, "AAPL", amount)


async def test_a_bar_without_an_open_waits_for_the_next_one(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    for day, row in (("2026-09-22", (date(2026, 9, 22), None, 100.0, 100.0, 100.0, 100.0, 1000)),
                     ("2026-09-23", ohlc("2026-09-23", 101.0, 101.0))):
        yahoo.series["AAPL"].bars.append(row)
        yahoo.series["GBPUSD=X"].bars.append(bar(day, 1.25))
        clock.advance(86400)
        await update_listing(ctx, "AAPL")
        await update_fx(ctx)
        outcomes = fill_pending(ctx)
    [outcome] = outcomes
    assert outcome.status == "filled"
    assert PaperRepo(ctx.conn).fills("manual")[0].date == date(2026, 9, 23)


async def test_a_stop_on_an_out_of_date_lean_logs_only_the_sell(ctx, yahoo, clock):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy")
    strategy_step(ctx)
    await next_day(ctx, yahoo, clock, aapl_open=100.0)
    fill_pending(ctx)
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-23", 50.0, 50.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)
    [d] = strategy_step(ctx)
    assert d.action == "sell" and d.reason.startswith("stop")


async def test_a_listing_gone_from_the_universe_sells_but_is_not_bought(ctx, yahoo, clock, monkeypatch):
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    place_sell(ctx, "AAPL", None)
    place_buy(ctx, "AAPL", 1000.0)
    monkeypatch.setattr(UniverseRepo, "profile", lambda self, symbol: None)
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-23", 100.0, 100.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)
    outcomes = {o.order.side: o for o in fill_pending(ctx)}
    assert outcomes["sell"].status == "filled"
    assert (outcomes["buy"].status, outcomes["buy"].note) == ("rejected", "AAPL is no longer in the universe")


async def test_a_pending_order_with_an_out_of_date_lean_logs_one_skip(ctx, yahoo, clock):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy")
    strategy_step(ctx)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    place_sell(ctx, "AAPL", None, account="strategy")
    [d] = strategy_step(ctx)
    assert (d.action, d.reason) == ("skip", "an order is already pending")


async def test_a_delisted_buy_is_rejected_without_waiting_for_a_bar(ctx, monkeypatch):
    place_buy(ctx, "AAPL", 1000.0)
    monkeypatch.setattr(UniverseRepo, "profile", lambda self, symbol: None)
    [outcome] = fill_pending(ctx)
    assert outcome.status == "rejected"


async def test_decisions_before_a_failing_order_stay_logged(ctx, monkeypatch):
    import oracle.services.paper_service as paper

    for s in ("AAPL", "VOD.L"):
        watch(ctx, s)
        seed(ctx, s, "buy", probability=0.9 if s == "AAPL" else 0.8)
    real = paper.place_buy
    monkeypatch.setattr(paper, "place_buy", lambda c, s, *a, **k: real(c, s, *a, **k) if s == "AAPL" else 1 / 0)
    with pytest.raises(ZeroDivisionError):
        strategy_step(ctx)
    assert [(d.symbol, d.action) for d in decision_log(ctx, "strategy")] == [("AAPL", "buy")]


def delist(ctx, symbol):
    with ctx.conn:
        ctx.conn.execute("DELETE FROM listings WHERE symbol = ?", (symbol,))


async def test_a_held_listing_gone_from_the_universe_can_still_be_sold(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    delist(ctx, "AAPL")
    assert place_sell(ctx, "aapl", None).symbol == "AAPL"
    with pytest.raises(UnknownSymbol):
        place_sell(ctx, "MSFT", None)


async def test_a_step_skips_a_watched_listing_gone_from_the_universe(ctx):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy")
    delist(ctx, "AAPL")
    [d] = strategy_step(ctx)
    assert (d.action, d.reason) == ("skip", "no longer in the universe")


async def test_a_pending_buy_without_a_lean_is_explained_as_pending(ctx):
    watch(ctx, "AAPL")
    place_buy(ctx, "AAPL", 1000.0, account="strategy", source="strategy")
    [d] = strategy_step(ctx)
    assert d.reason == "an order is already pending"


async def test_each_sell_records_its_own_realised_result_by_hand(ctx, yahoo, clock):
    from oracle.services.paper_service import sells

    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    for day, price in (("2026-09-23", 110.0), ("2026-09-24", 90.0)):
        place_sell(ctx, "AAPL", 6)
        yahoo.series["AAPL"].bars.append(ohlc(day, price, price))
        yahoo.series["GBPUSD=X"].bars.append(bar(day, 1.25))
        clock.advance(86400)
        await update_listing(ctx, "AAPL")
        await update_fx(ctx)
        fill_pending(ctx)
    first, second = sells(ctx, "manual")
    # Same worked example as the partial sell above: 6 of 12 shares at 110 * 0.999.
    proceeds, half_cost = 658.34 * 0.998 / 1.25, 1202.2 / 1.2475 / 2
    assert (first.date, first.symbol, first.shares) == (date(2026, 9, 23), "AAPL", 6)
    assert (first.proceeds, first.cost) == (pytest.approx(proceeds), pytest.approx(half_cost))
    assert first.realised == pytest.approx(proceeds - half_cost)
    # The rest at 90 * 0.999 = 89.91: 6 * 89.91 = 539.46 - 1 fee = 538.46, less the spread.
    assert second.proceeds == pytest.approx(538.46 * 0.998 / 1.25)
    assert second.cost == pytest.approx(half_cost)
    assert first.realised + second.realised == pytest.approx(account_view(ctx, "manual").realised)


async def test_an_account_names_the_rate_sources_behind_its_position_values(ctx, yahoo, clock):
    assert account_view(ctx, "manual").rate_sources == ()
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    assert account_view(ctx, "manual").rate_sources == ("yahoo",)


async def test_rates_used_by_past_fills_are_still_credited_once_nothing_is_held(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    place_sell(ctx, "AAPL", None)
    yahoo.series["AAPL"].bars.append(ohlc("2026-09-23", 110.0, 110.0))
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-23", 1.25))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)
    fill_pending(ctx)
    view = account_view(ctx, "manual")
    assert view.positions == () and view.rate_sources == ("yahoo",)


def ai_on(ctx, **changes):
    ctx.config = replace(ctx.config, ai_account=replace(ctx.config.ai_account, enabled=True, **changes))


def verdict(ctx, symbol, label, confidence=0.7, day=date(2026, 9, 21), rejected=None):
    """A finished live research run whose report carries this verdict (or why it has none)."""
    from oracle.research.trace import Report, RunStatus, Verdict
    from oracle.store.research import ResearchRepo

    repo = ResearchRepo(ctx.conn)
    run = repo.start(symbol, "ollama", "m", f"{day.isoformat()}T12:00:00Z")
    call = None if rejected else Verdict(label, confidence, "Because [a1]", (1,), "it falls")
    repo.save_report(run, symbol, Report(day, (), None, None, (), None, verdict=call, verdict_rejected=rejected))
    repo.finish(run, RunStatus.DONE, "notes", None, f"{day.isoformat()}T12:05:00Z")
    return run


async def test_the_ai_account_starts_level_and_its_equity_is_recorded_only_while_it_is_on(ctx, yahoo, clock):
    assert account_view(ctx, "ai").cash == 10_000.0
    fill_pending(ctx)
    assert equity_history(ctx, "ai") == [] and equity_history(ctx, "strategy") != []
    assert comparison(ctx).start is None
    await next_day(ctx, yahoo, clock)
    ai_on(ctx)
    fill_pending(ctx)
    assert equity_history(ctx, "ai") == [(date(2026, 9, 22), 10_000.0)]
    assert comparison(ctx).start == date(2026, 9, 22)


async def test_while_off_the_ai_step_does_nothing(ctx):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy")
    verdict(ctx, "AAPL", "buy", 0.9)
    assert ai_step(ctx) is None
    assert account_view(ctx, "ai").pending == () and decision_log(ctx, "ai") == []


async def test_the_ai_step_buys_on_a_buy_verdict_under_the_strategy_rules_and_logs_the_report(ctx):
    for s in ("AAPL", "VOD.L"):
        watch(ctx, s)
        seed(ctx, s, "sell")  # the leans say sell: the AI account never reads them
    ai_on(ctx)
    run = verdict(ctx, "AAPL", "buy", 0.8)
    low = verdict(ctx, "VOD.L", "buy", 0.5)
    decisions = {d.symbol: d for d in ai_step(ctx)}
    assert decisions["AAPL"].action == "buy" and decisions["AAPL"].cash == pytest.approx(500.0)
    assert decisions["AAPL"].reason == "ai lean buy at 80%"
    assert decisions["VOD.L"].action == "skip" and decisions["VOD.L"].reason.startswith("probability 50%")
    [order] = account_view(ctx, "ai").pending
    assert (order.symbol, order.source) == ("AAPL", "ai")
    logged = {d.symbol: d for d in decision_log(ctx, "ai")}
    assert (logged["AAPL"].report_id, logged["AAPL"].order_id) == (run, order.id)
    assert (logged["VOD.L"].report_id, logged["VOD.L"].account) == (low, "ai")
    assert account_view(ctx, "strategy").pending == () and decision_log(ctx, "strategy") == []


async def test_the_ai_step_sells_on_a_sell_verdict_and_holds_on_hold(ctx, yahoo, clock):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy")
    ai_on(ctx)
    verdict(ctx, "AAPL", "buy", 0.9)
    ai_step(ctx)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    later = verdict(ctx, "AAPL", "hold", 0.9, day=date(2026, 9, 22))
    assert ai_step(ctx) == [] and decision_log(ctx, "ai")[0].report_id != later
    run = verdict(ctx, "AAPL", "sell", 0.6, day=date(2026, 9, 22))
    [d] = ai_step(ctx)
    assert (d.action, d.reason) == ("sell", "ai lean is sell")
    assert account_view(ctx, "ai").pending[0].side == "sell" and decision_log(ctx, "ai")[0].report_id == run
    assert account_view(ctx, "strategy").positions == ()


async def test_the_ai_account_keeps_the_position_limit(ctx):
    ctx.config = replace(ctx.config, strategy=replace(ctx.config.strategy, max_positions=1))
    ai_on(ctx)
    for s, confidence in (("AAPL", 0.9), ("VOD.L", 0.8)):
        watch(ctx, s)
        seed(ctx, s, "hold")
        verdict(ctx, s, "buy", confidence)
    decisions = {d.symbol: d for d in ai_step(ctx)}
    assert decisions["AAPL"].action == "buy"
    assert (decisions["VOD.L"].action, decisions["VOD.L"].reason) == ("skip", "already 1 positions")


async def test_the_ai_step_skips_what_it_has_no_usable_verdict_for(ctx, clock):
    ai_on(ctx, max_verdict_age_days=3)
    for s in ("AAPL", "VOD.L"):
        watch(ctx, s)
        seed(ctx, s, "buy")
    [d] = [d for d in ai_step(ctx) if d.symbol == "AAPL"]
    assert (d.action, d.reason) == ("skip", "no verdict yet: run research on it")
    rejected = verdict(ctx, "AAPL", "", rejected="its reason cites no evidence")
    [d] = [d for d in ai_step(ctx) if d.symbol == "AAPL"]
    assert d.reason == "no verdict: the verdict was rejected: its reason cites no evidence"
    assert {x.symbol: x.report_id for x in decision_log(ctx, "ai", limit=2)}["AAPL"] == rejected
    old = verdict(ctx, "VOD.L", "buy", 0.9, day=date(2026, 9, 17))
    [d] = [d for d in ai_step(ctx) if d.symbol == "VOD.L"]
    assert d.reason == "the verdict is from 2026-09-17, older than 3 days: run research on it again"
    assert {x.symbol: x.report_id for x in decision_log(ctx, "ai", limit=2)}["VOD.L"] == old
    delist(ctx, "VOD.L")
    [d] = [d for d in ai_step(ctx) if d.symbol == "VOD.L"]
    assert d.reason == "no longer in the universe"
    assert account_view(ctx, "ai").pending == ()


async def test_a_newer_rejected_verdict_supersedes_an_older_valid_one(ctx):
    watch(ctx, "AAPL")
    ai_on(ctx)
    verdict(ctx, "AAPL", "buy", 0.9, day=date(2026, 9, 20))
    newer = verdict(ctx, "AAPL", "", rejected="its reason cites no evidence")
    [d] = ai_step(ctx)
    assert (d.action, d.reason) == ("skip", "no verdict: the verdict was rejected: its reason cites no evidence")
    assert decision_log(ctx, "ai")[0].report_id == newer and account_view(ctx, "ai").pending == ()


async def test_a_strategy_step_never_touches_the_ai_account(ctx):
    watch(ctx, "AAPL")
    seed(ctx, "AAPL", "buy")
    ai_on(ctx)
    verdict(ctx, "AAPL", "sell")
    strategy_step(ctx)
    assert account_view(ctx, "ai").pending == () and decision_log(ctx, "ai") == []
    assert account_view(ctx, "strategy").pending[0].symbol == "AAPL"


async def test_with_no_ai_history_the_accounts_are_compared_from_their_own_starts(ctx):
    from oracle.store.paper import EquityRepo

    EquityRepo(ctx.conn).record("strategy", date(2026, 9, 18), 10_000.0, 0.0)
    EquityRepo(ctx.conn).record("strategy", date(2026, 9, 21), 10_500.0, 0.0)
    c = comparison(ctx)
    assert c.start is None and any("the ai account has no history yet" in n.lower() for n in c.notes)
    lines = {line.name: line for line in c.lines}
    assert list(lines) == ["strategy", "ai", "manual", "buy_and_hold"]
    assert lines["strategy"].points == ((date(2026, 9, 18), 0.0), (date(2026, 9, 21), pytest.approx(0.05)))
    assert lines["ai"].points == () and lines["ai"].return_ is None and lines["buy_and_hold"].points == ()


async def test_lines_since_the_ai_started_with_buy_and_hold_of_the_watchlist_by_hand(ctx, yahoo, clock):
    from oracle.store.paper import EquityRepo

    watch(ctx, "AAPL")
    watch(ctx, "VOD.L")
    EquityRepo(ctx.conn).record("strategy", date(2026, 9, 18), 5_000.0, 0.0)  # before the AI started: dropped
    place_buy(ctx, "AAPL", 1000.0)
    ai_on(ctx)
    fill_pending(ctx)  # 09-21: the AI account's first record
    await next_day(ctx, yahoo, clock, aapl_open=110.0)
    fill_pending(ctx)
    c = comparison(ctx)
    assert c.start == date(2026, 9, 21) and c.left_out == ()
    lines = {line.name: line for line in c.lines}
    assert lines["strategy"].points == ((date(2026, 9, 21), 0.0), (date(2026, 9, 22), 0.0))
    manual = lines["manual"]
    assert (manual.trades, manual.costs) == (1, pytest.approx(account_score(ctx, "manual").costs))
    assert manual.return_ == pytest.approx(account_score(ctx, "manual").equity / 10_000 - 1)
    # Half in AAPL, up 10%; half in VOD.L, which has no bar on 09-22 and keeps its 09-21 value.
    hold = lines["buy_and_hold"]
    assert hold.points == ((date(2026, 9, 21), 0.0), (date(2026, 9, 22), pytest.approx(0.05)))
    assert (hold.trades, hold.costs, hold.max_fall) == (0, 0.0, 0.0)
    assert "2026-09-21" in hold.caption and any("validated" in n for n in c.notes)


async def test_a_watched_listing_without_prices_is_left_out_of_buy_and_hold_and_named(ctx, yahoo, clock):
    watch(ctx, "AAPL")
    watch(ctx, "VOD.L")
    delist(ctx, "VOD.L")
    ai_on(ctx)
    fill_pending(ctx)
    c = comparison(ctx)
    assert c.left_out == ("VOD.L",) and any("VOD.L" in n for n in c.notes)
    assert {line.name: line for line in c.lines}["buy_and_hold"].points == ((date(2026, 9, 21), 0.0),)
