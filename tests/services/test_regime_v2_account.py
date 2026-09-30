"""The live regime-v2 account: the regime account's plumbing with its own switch, the v2 book,
each holding's first fill day handed to the rotation, and rotation sales logged with whom they
made room for."""

from dataclasses import replace
from datetime import date

from oracle.broker.regime import ROTATION, Rotation
from oracle.services.paper_service import (
    ACCOUNTS, LINE_NAMES, TRADERS, account_view, decision_log, fill_pending, held_since, traded_by,
)
from oracle.services.regime_service import REGIME_V2_OFF, RegimeRun, build_book, regime_card, regime_step
from oracle.services.watch_service import watch
from oracle.broker.orders import Action, Side
from oracle.broker.rules import Decision
from oracle.services.tick_service import line_summary
from oracle.store.paper import DecisionRepo, FillRow, LoggedDecision, PaperRepo
from tests.fakes.regime import use_stub
from tests.services.test_paper import clock, ctx, next_day, seed, yahoo  # noqa: F401
from tests.services.test_paper_discovered import ai_trades, found
from tests.services.test_regime_account import PARTS

DAY = date(2026, 9, 21)


def step(ctx):
    if ctx.clock.utcnow().date() == DAY:
        ctx.clock.advance(86400)
    return regime_step(ctx, "regime-v2")


def v2_on(ctx, on):
    ctx.config = replace(ctx.config, regime_v2_account=replace(ctx.config.regime_v2_account, enabled=on))


def test_regime_v2_is_listed_trades_the_same_set_and_is_on_by_default(ctx):
    assert "regime-v2" in ACCOUNTS and "regime-v2" in TRADERS and LINE_NAMES["regime-v2"] == "Regime-v2"
    assert ctx.config.regime_v2_account.enabled is True
    assert account_view(ctx, "regime-v2").cash == ctx.config.risk.account
    watch(ctx, "AAPL")
    ai_trades(ctx, True)
    found(ctx, "VOD.L")
    assert traded_by(ctx, "regime-v2") == traded_by(ctx, "regime")


def test_off_it_decides_nothing_and_says_so(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    watch(ctx, "AAPL")
    v2_on(ctx, False)
    assert step(ctx) is None
    assert built == [] and decision_log(ctx, "regime-v2") == []
    card = regime_card(ctx, account="regime-v2")
    assert (card.enabled, card.note) == (False, REGIME_V2_OFF) and "[regime_v2_account]" in REGIME_V2_OFF


def test_it_builds_the_v2_book_and_its_decisions_are_its_own(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    run = step(ctx)
    assert (run.account, run.day, built[0].account) == ("regime-v2", DAY, "regime-v2")
    [order] = account_view(ctx, "regime-v2").pending
    assert (order.symbol, order.source) == ("AAPL", "regime-v2")
    assert decision_log(ctx, "regime") == [] and account_view(ctx, "regime").pending == ()


def test_the_real_book_it_builds_is_the_v2_one(ctx):
    v1, v2 = (build_book(ctx, {}, None, account=a) for a in ("regime", "regime-v2"))
    assert (v1._settings.spread_unscored, v1._settings.rotation) == (False, None)
    assert (v2._settings.spread_unscored, v2._settings.rotation, v2._source) == (True, Rotation(), "regime-v2")


async def test_the_cap_gets_each_holdings_first_fill_day_and_a_rotation_is_logged(ctx, yahoo, clock, monkeypatch):
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    built_first = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    regime_step(ctx, "regime-v2")
    assert built_first[0].since == [{}]
    await next_day(ctx, yahoo, clock)
    [done] = fill_pending(ctx)
    [fill] = PaperRepo(ctx.conn).fills("regime-v2")
    filled = fill.date
    assert done.status == "filled"
    assert held_since(PaperRepo(ctx.conn), "regime-v2") == {"AAPL": filled}
    built = use_stub(monkeypatch, leans={"AAPL": ("hold", 0.5, PARTS)}, rotated=frozenset({"AAPL"}))
    run = regime_step(ctx, "regime-v2")
    assert built[0].since == [{"AAPL": filled}]
    [sold] = [d for d in run.decisions if d.symbol == "AAPL"]
    assert sold.action == "sell" and sold.reason.startswith(ROTATION) and "make room for X" in sold.reason
    [logged] = [d for d in decision_log(ctx, "regime-v2") if d.action == "sell"]
    assert logged.reason == sold.reason


def test_a_pending_buy_reaches_the_cap_with_no_day(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    step(ctx)
    step(ctx)
    assert built[1].since == [{"AAPL": None}]


def test_the_card_names_its_rotation_rule(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("hold", 0.5, PARTS)})
    watch(ctx, "AAPL")
    step(ctx)
    card = regime_card(ctx, account="regime-v2")
    assert (card.account, card.day, card.rotation) == ("regime-v2", DAY, {"min_hold": 20, "margin": 0.25, "most": 2})
    assert regime_card(ctx).rotation is None and regime_card(ctx, account="regime-ai").rotation is None


def test_the_card_reports_rotations_a_busy_steps_decision_window_would_hide(ctx):
    day = date(2026, 9, 22)
    rows = [LoggedDecision("regime-v2", day, "AAPL", Action.SELL, None, f"{ROTATION}: sold to make room for X", None),
           LoggedDecision("regime-v2", day, "MSFT", Action.SELL, None, f"{ROTATION}: sold to make room for Y", None)]
    rows += [LoggedDecision("regime-v2", day, f"S{i}", Action.SKIP, None, "already 10 positions", None)
            for i in range(25)]
    DecisionRepo(ctx.conn).log(rows)
    card = regime_card(ctx, account="regime-v2")
    assert card.rotated_last_step == 2


def test_the_tick_summary_counts_the_steps_rotations(ctx):
    sold = Decision("AAPL", Action.SELL, None, f"{ROTATION}: sold to make room for X")
    run = RegimeRun(DAY, "calm_up", (), [sold, Decision("KO", Action.SELL, None, "stop: 9% below cost")],
                    "regime-v2")
    assert "1 rotated out for a stronger buy" in line_summary(ctx, run)
    assert "rotated" not in line_summary(ctx, RegimeRun(DAY, "calm_up", (), [], "regime-v2"))


def test_a_holding_is_dated_from_its_first_fill_since_it_was_last_flat():
    class Fills:
        def fills(self, account, source=None):
            rows = [("A", "buy", 5, 1), ("A", "buy", 5, 3), ("B", "buy", 5, 4), ("A", "sell", 10, 6),
                    ("A", "buy", 2, 8), ("B", "sell", 2, 9)]
            return [FillRow(1, account, s, Side(side), date(2026, 9, d), n, 1.0, "GBP", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                    for s, side, n, d in rows]

    assert held_since(Fills(), "regime-v2") == {"A": date(2026, 9, 8), "B": date(2026, 9, 4)}
