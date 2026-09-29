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
from oracle.services.regime_service import DISCOVERED_OFF, NO_MOOD, REGIME_OFF, regime_card, regime_step
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
    assert (d.action, d.reason) == ("skip", "no regime score: too little history for any method")


def test_a_stale_listing_is_not_leaned_on(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS), "VOD.L": ("buy", 0.9, PARTS)})
    BarRepo(ctx.conn).upsert([Bar("MSFT", date(2026, 10, 12), 1, 1, 1, 1, 1, 1, "GBP", "t")], "x")
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
    assert {s: (v.label, v.probability) for s, v in inputs.verdicts.items()} == {"AAPL": ("buy", 0.8)}
    assert inputs.earnings == {"AAPL": (date(2026, 7, 30),)}
    assert inputs.factors["AAPL"] == {"value": ((date(2026, 9, 18), 0.4),), "quality": ()}
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
