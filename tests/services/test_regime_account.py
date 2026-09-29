"""The live regime account: its step trades a regime book's leans under the strategy's rules on the
same set as the AI account, logs each decision with its explanation and saves the day's reading."""

import json
from dataclasses import replace
from datetime import date

import pytest

from oracle.analysts.signals import AnalystSignal
from oracle.market.bars import Bar
from oracle.services.paper_service import (
    ACCOUNTS, NEVER_BUY, TRADERS, account_view, comparison, decision_log, equity_history, fill_pending, traded_by,
)
from oracle.services.prices_service import ConvertedBar
from oracle.services.regime_service import (
    DISCOVERED_OFF, NO_MOOD, NO_SCORE, REGIME_OFF, complete_sessions, regime_card, regime_step,
)
from oracle.services.watch_service import watch
from oracle.store.bars import BarRepo
from oracle.store.meta import MetaRepo
from oracle.store.signals import SignalRepo
from tests.fakes.regime import use_stub
from tests.services.test_paper import clock, ctx, next_day, seed, verdict, yahoo  # noqa: F401
from tests.services.test_paper_discovered import ai_trades, found

PARTS = {"trend": 0.1, "momentum": 0.25, "hold": 0.05, "reversion": -0.02}


def regime(ctx, on):
    ctx.config = replace(ctx.config, regime_account=replace(ctx.config.regime_account, enabled=on))


def test_the_regime_account_is_listed_trades_and_is_on_by_default(ctx):
    assert "regime" in ACCOUNTS and "regime" in TRADERS
    assert ctx.config.regime_account.enabled is True
    assert account_view(ctx, "regime").cash == ctx.config.risk.account


def test_off_it_decides_nothing_and_says_so(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    watch(ctx, "AAPL")
    regime(ctx, False)
    assert regime_step(ctx) is None
    assert built == [] and decision_log(ctx, "regime") == [] and account_view(ctx, "regime").pending == ()
    card = regime_card(ctx)
    assert (card.enabled, card.note) == (False, REGIME_OFF) and "Settings" in REGIME_OFF


def test_a_buy_lean_is_queued_and_logged_with_its_mood_and_parts(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)}, flags=frozenset({"choppy"}))
    seed(ctx, "AAPL", "hold")  # a risk size to buy with
    watch(ctx, "AAPL")
    run = regime_step(ctx)
    assert (run.day, run.mood, run.flags) == (date(2026, 9, 21), "calm_up", ("choppy",))
    [d] = run.decisions
    assert d.action == "buy"
    [order] = account_view(ctx, "regime").pending
    assert (order.symbol, order.source) == ("AAPL", "regime")
    [logged] = decision_log(ctx, "regime")
    why = logged.explanation
    assert (why["mood"], why["flags"], why["label"], why["probability"]) == ("calm_up", ["choppy"], "buy", 0.9)
    assert list(why["parts"]) == ["momentum", "trend", "hold", "reversion"], "largest part first"
    assert why["score"] == pytest.approx(sum(PARTS.values()))
    assert decision_log(ctx, "ai") == [] and decision_log(ctx, "strategy") == []


def test_the_days_reading_is_saved_for_the_card(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("hold", 0.5, PARTS)})
    watch(ctx, "AAPL")
    regime_step(ctx)
    card = regime_card(ctx)
    assert (card.enabled, card.note, card.day) == (True, None, date(2026, 9, 21))
    s = card.state
    assert (s["mood"], s["flags"], s["warnings"], s["day"]) == ("calm_up", [], {"momentum": ["rebound"]}, "2026-09-21")
    assert set(s) >= {"readings", "base_weights", "weights", "good", "health", "tilt"}
    json.dumps(s)
    assert card.decisions == (), "a hold lean on a listing not held changes nothing, so nothing is logged"


def test_with_no_mood_every_listing_leans_nothing_and_the_card_says_why(ctx, monkeypatch):
    use_stub(monkeypatch, mood=None, leans={"AAPL": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    run = regime_step(ctx)
    assert run.mood is None
    assert [(d.symbol, d.action, d.reason) for d in run.decisions] == [("AAPL", "skip", NO_MOOD)]
    assert account_view(ctx, "regime").pending == ()
    assert regime_card(ctx).note == NO_MOOD and regime_card(ctx).state["mood"] is None


def test_a_listing_with_no_score_is_skipped_saying_why(ctx, monkeypatch):
    use_stub(monkeypatch, leans={})
    watch(ctx, "AAPL")
    [d] = regime_step(ctx).decisions
    assert (d.action, d.reason) == ("skip", NO_SCORE)


def test_a_stale_listing_is_not_leaned_on(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS), "VOD.L": ("buy", 0.9, PARTS)})
    # Three other listings trade on: the market's day moves on while AAPL's prices stop.
    for s in ("MSFT", "KO", "IBM"):
        BarRepo(ctx.conn).upsert([Bar(s, date(2026, 10, 12), 1, 1, 1, 1, 1, 1, "GBP", "t")], "x")
    ctx.clock.advance(21 * 86400)
    watch(ctx, "AAPL")
    [d] = regime_step(ctx).decisions
    assert (d.action, d.reason) == ("skip", "no price since 2026-09-21: update its prices")
    assert built[0].asked == []


def test_bars_after_today_are_never_read(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    BarRepo(ctx.conn).upsert([Bar("AAPL", date(2026, 9, 22), 500, 500, 500, 500, 500, 1, "USD", "t")], "x")
    watch(ctx, "AAPL")
    run = regime_step(ctx)
    assert run.day == date(2026, 9, 21)
    assert max(c.bar.date for rows in built[0].history.values() for c in rows) == date(2026, 9, 21)
    assert built[0].asked == [("AAPL", date(2026, 9, 21))]


def test_the_book_reads_the_whole_stored_market_but_trades_only_its_set(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS), "VOD.L": ("buy", 0.9, PARTS)})
    watch(ctx, "AAPL")
    run = regime_step(ctx)
    assert set(built[0].history) >= {"AAPL", "VOD.L"}
    assert [d.symbol for d in run.decisions] == ["AAPL"]


def test_it_trades_the_ai_accounts_set_discovered_behind_the_same_switch(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"VOD.L": ("buy", 0.9, PARTS)})
    watch(ctx, "AAPL")
    found(ctx, "VOD.L")
    assert traded_by(ctx, "regime") == traded_by(ctx, "ai") == ["AAPL", "VOD.L"]
    ai_trades(ctx, False)
    assert traded_by(ctx, "regime") == ["AAPL"]
    decisions = {d.symbol: d for d in regime_step(ctx).decisions}
    assert (decisions["VOD.L"].action, decisions["VOD.L"].reason) == ("skip", DISCOVERED_OFF)


def test_the_never_list_bars_its_buys_like_the_ai_accounts(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"VOD.L": ("buy", 0.9, PARTS)})
    found(ctx, "VOD.L")
    seed(ctx, "VOD.L", "hold")
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, never=("Vodafone",)))
    [d] = regime_step(ctx).decisions
    assert (d.symbol, d.action, d.reason) == ("VOD.L", "skip", NEVER_BUY)
    assert account_view(ctx, "regime").pending == ()


def test_the_book_gets_sectors_earnings_factor_scores_and_only_fresh_verdicts(ctx, monkeypatch):
    built = use_stub(monkeypatch)
    watch(ctx, "AAPL")
    watch(ctx, "VOD.L")
    verdict(ctx, "AAPL", "buy", 0.8)
    verdict(ctx, "VOD.L", "sell", 0.8, day=date(2026, 9, 1))  # older than the 7 days
    MetaRepo(ctx.conn).set("earnings_dates:AAPL", json.dumps(
        {"source": "SEC", "fetched_at": "2026-09-20T00:00:00Z", "releases": [["2026-07-30", False]]}))
    SignalRepo(ctx.conn).save([AnalystSignal("value", "AAPL", date(2026, 9, 18), 0.4, 1.0, "v", ())], "x")
    regime_step(ctx)
    inputs = built[0].inputs
    assert inputs.verdicts == {"AAPL": ((date(2026, 9, 21), 0.5),)} and inputs.verdict_max_age == 7
    assert inputs.earnings == {"AAPL": ((date(2026, 7, 30), False),)}
    assert inputs.factors == {"AAPL": ((date(2026, 9, 18), 0.4),)}
    assert set(inputs.sectors) <= {"AAPL", "VOD.L"}


async def test_its_equity_is_recorded_only_while_on(ctx, yahoo, clock):
    regime(ctx, False)
    fill_pending(ctx)
    assert equity_history(ctx, "regime") == []
    await next_day(ctx, yahoo, clock)
    regime(ctx, True)
    fill_pending(ctx)
    assert equity_history(ctx, "regime") == [(date(2026, 9, 22), 10_000.0)]
    line = next(line for line in comparison(ctx).lines if line.name == "regime")
    assert line.caption.startswith("Regime:")


async def test_its_buy_fills_at_the_next_open_like_the_other_accounts(ctx, yahoo, clock, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    regime_step(ctx)
    await next_day(ctx, yahoo, clock)
    [done] = fill_pending(ctx)
    assert (done.order.account, done.status) == ("regime", "filled")
    assert [p.symbol for p in account_view(ctx, "regime").positions] == ["AAPL"]


def test_the_sector_cap_counts_holdings_and_pending_buys_and_a_capped_lean_is_not_bought(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS), "VOD.L": ("buy", 0.9, PARTS)},
                     capped=frozenset({"VOD.L"}))
    for s in ("AAPL", "VOD.L"):
        seed(ctx, s, "hold")
        watch(ctx, s)
    decisions = {d.symbol: d.action for d in regime_step(ctx).decisions}
    assert decisions == {"AAPL": "buy"} and built[0].held == [set()]
    regime_step(ctx)
    assert built[1].held == [{"AAPL"}], "the pending buy counts against its sector"


def priced(symbol, day, n=1):
    return ConvertedBar(Bar(symbol, day, 1, 1, 1, 1, 1, 1, "GBP", "t"), 1.0, 1.0)


def test_a_trailing_day_only_some_exchanges_have_closed_is_left_out():
    d1, d2, d3 = date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28)
    history = {f"S{k}": [priced(f"S{k}", d1), priced(f"S{k}", d2)] + ([priced(f"S{k}", d3)] if k < 4 else [])
               for k in range(10)}
    kept, day = complete_sessions(history)
    assert day == d2 and all(rows[-1].bar.date == d2 for rows in kept.values())
    history["S9"].append(priced("S9", d3))
    history.update({f"T{k}": [priced(f"T{k}", d3)] for k in range(8)})
    assert complete_sessions(history)[1] == d3, "most of the market has the day: it is complete"
    assert complete_sessions({}) == ({}, None)


def test_the_real_regime_book_reads_a_mood_and_explains_each_lean(ctx, monkeypatch):
    from oracle.services import regime_service
    from tests.analysts.bars import weekdays

    books, real = [], regime_service.build_book
    monkeypatch.setattr(regime_service, "build_book", lambda *a: books.append(real(*a)) or books[-1])
    from tests.broker.regime_market import market

    n = 700
    days = [d for d in weekdays(n + 400, date(2023, 1, 2)) if d <= date(2026, 9, 21)][-n:]
    names = {"L000": "AAPL", "L001": "VOD.L"}
    repo = BarRepo(ctx.conn)
    for s, bars in market(12, n, seed=5).items():
        symbol = names.get(s, s)
        repo.upsert([replace(b, symbol=symbol, date=d, currency="GBP") for b, d in zip(bars, days)], "x")
    for s in ("AAPL", "VOD.L"):
        seed(ctx, s, "hold")
        watch(ctx, s)
    run = regime_step(ctx)
    assert run.day == date(2026, 9, 21) and run.mood in ("calm_up", "volatile_up", "calm_down", "volatile_down")
    assert all(d.reason not in (NO_MOOD, NO_SCORE) for d in run.decisions)
    for s in ("AAPL", "VOD.L"):
        x = books[0].explain(s, run.day)
        assert x.state.mood == run.mood and sum(x.parts.values()) == pytest.approx(x.score)
    for d in decision_log(ctx, "regime"):
        assert d.explanation["mood"] == run.mood
        assert sum(d.explanation["parts"].values()) == pytest.approx(d.explanation["score"])
    card = regime_card(ctx)
    assert card.note is None and card.state["mood"] == run.mood
    assert sum(card.state["weights"].values()) == pytest.approx(1.0) and len(card.state["base_weights"]) == 8
