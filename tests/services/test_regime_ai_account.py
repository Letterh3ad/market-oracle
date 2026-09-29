"""The live regime-ai account: the regime account's book and traded set, with the day's model
tilt asked once per step day and stored, and a failure falling back to the rules weights."""

from dataclasses import replace
from datetime import date

import pytest

from oracle.broker.regime import METHODS
from oracle.services.regime_tilt import TILTED
from oracle.research.llm import ModelError
from oracle.services.paper_service import ACCOUNTS, TRADERS, account_view, decision_log, traded_by
from oracle.services.regime_service import REGIME_AI_OFF, lag_warning, regime_card, regime_step
from oracle.services.watch_service import watch
from oracle.store.regime import TiltRepo
from tests.fakes.regime import use_stub
from tests.fakes.tilt_model import TiltModel
from tests.services.test_paper import clock, ctx, seed, yahoo  # noqa: F401
from tests.services.test_paper_discovered import ai_trades, found

PARTS = {"trend": 0.1, "momentum": 0.25, "hold": 0.05}
ANSWER = {"momentum": 0.6, "trend": 1.4, "astrology": 2.0, "reason": "Momentum warned on a rebound."}
DAY = date(2026, 9, 21)


def step(ctx, model=None):
    """The step the next day, once the fixture's last session (2026-09-21) is known final."""
    if ctx.clock.utcnow().date() == DAY:
        ctx.clock.advance(86400)
    return regime_step(ctx, "regime-ai", model)


def test_the_regime_ai_account_is_listed_trades_the_same_set_and_is_on_by_default(ctx):
    assert "regime-ai" in ACCOUNTS and "regime-ai" in TRADERS
    assert ctx.config.regime_ai_account.enabled is True
    assert account_view(ctx, "regime-ai").cash == ctx.config.risk.account
    watch(ctx, "AAPL")
    ai_trades(ctx, True)
    found(ctx, "VOD.L")
    assert traded_by(ctx, "regime-ai") == traded_by(ctx, "regime") == traded_by(ctx, "ai")


def test_off_it_decides_nothing_asks_no_model_and_says_so(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    watch(ctx, "AAPL")
    ctx.config = replace(ctx.config, regime_ai_account=replace(ctx.config.regime_ai_account, enabled=False))
    model = TiltModel(ANSWER)
    assert step(ctx, model) is None
    assert built == [] and model.asked == [] and decision_log(ctx, "regime-ai") == []
    card = regime_card(ctx, account="regime-ai")
    assert (card.enabled, card.note) == (False, REGIME_AI_OFF) and "[regime_ai_account]" in REGIME_AI_OFF


def test_the_step_day_is_tilted_once_and_decisions_are_its_own(ctx, monkeypatch):
    built = use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    model = TiltModel(ANSWER)
    run = step(ctx, model)
    assert (run.account, run.day, run.lag) == ("regime-ai", DAY, None)
    [(messages, schema)] = model.asked
    assert "2026-09-21" in messages[1]["content"] and set(schema["required"]) == {*METHODS, "reason"} - {"ai_verdict"}
    assert run.tilt.tilt == {"momentum": 0.6, "trend": 1.4} and run.tilt.reason == "Momentum warned on a rebound."
    assert built[0].state(DAY).tilt == run.tilt.tilt
    [order] = account_view(ctx, "regime-ai").pending
    assert (order.symbol, order.source) == ("AAPL", "regime-ai")
    [logged] = decision_log(ctx, "regime-ai")
    assert logged.explanation["mood"] == "calm_up"
    assert decision_log(ctx, "regime") == [] and account_view(ctx, "regime").pending == ()


def test_a_second_step_that_day_reuses_the_stored_tilt(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("hold", 0.5, PARTS)})
    watch(ctx, "AAPL")
    first = step(ctx, TiltModel(ANSWER))
    again = TiltModel({"momentum": 1.5, "reason": "changed my mind"})
    second = step(ctx, again)
    assert again.asked == [] and second.tilt == first.tilt


def test_a_failed_tilt_trades_the_rules_weights_and_the_card_shows_why(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    run = step(ctx, TiltModel(error=ModelError("Ollama is not reachable at http://127.0.0.1:11434")))
    assert run.tilt.tilt == {} and "not reachable" in run.tilt.error
    assert [d.action for d in run.decisions] == ["buy"], "the rules still trade"
    card = regime_card(ctx, account="regime-ai")
    assert card.tilt["tilt"] == {} and "not reachable" in card.tilt["error"] and card.state["tilt"] == {}


def test_the_card_shows_the_tilt_next_to_the_rules_weights_and_the_model(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("hold", 0.5, PARTS)})
    watch(ctx, "AAPL")
    step(ctx, TiltModel(ANSWER))
    card = regime_card(ctx, account="regime-ai")
    assert (card.account, card.day, card.note) == ("regime-ai", DAY, None)
    assert card.tilt["tilt"] == {"momentum": 0.6, "trend": 1.4} and card.tilt["reason"] == ANSWER["reason"]
    assert card.tilt["rules"] == pytest.approx({m: 1 / len(METHODS) for m in METHODS})
    assert (card.tilt["model"], card.tilt["day"]) == ("fake tilt-1", "2026-09-21")
    assert card.state["tilt"] == {"momentum": 0.6, "trend": 1.4}
    assert (card.model, card.research_profile) == ("", "default")
    ctx.config = replace(ctx.config, regime_ai_account=replace(ctx.config.regime_ai_account, model="small"))
    assert regime_card(ctx, account="regime-ai").model == "small"
    assert regime_card(ctx).tilt is None and regime_card(ctx).model is None, "the rules line has neither"


def test_without_a_fake_the_configured_model_is_asked_and_its_answer_stored(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("hold", 0.5, PARTS)})
    watch(ctx, "AAPL")
    run = step(ctx)
    assert (run.tilt.tilt, run.tilt.model, run.tilt.error) == ({"hold": 1.0}, "stand-in", None)
    assert TiltRepo(ctx.conn).get("regime-ai", DAY) == run.tilt


@pytest.mark.parametrize(("day", "today", "warned"), [
    (date(2026, 9, 25), date(2026, 9, 28), False),  # Monday reads Friday
    (date(2026, 9, 24), date(2026, 9, 28), False),  # a holiday Friday: one weekday behind
    (date(2026, 9, 23), date(2026, 9, 28), False),  # two
    (date(2026, 9, 22), date(2026, 9, 28), True),   # three
    (date(2026, 9, 21), date(2026, 9, 26), True),
])
def test_a_step_day_over_two_weekdays_behind_yesterday_is_warned_about(day, today, warned):
    said = lag_warning(day, today)
    assert (said is not None) == warned
    if warned:
        assert day.isoformat() in said and "weekdays behind yesterday" in said


def test_a_lagging_step_says_so_in_its_run_and_on_the_card(ctx, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("hold", 0.5, PARTS)})
    watch(ctx, "AAPL")
    ctx.clock.advance(5 * 86400)
    run = regime_step(ctx)
    assert run.day == DAY and "4 weekdays behind" in run.lag
    assert regime_card(ctx).lag == run.lag


def test_the_real_book_tilted_moves_the_weights_the_rules_line_keeps(ctx, monkeypatch):
    from tests.analysts.bars import weekdays
    from tests.broker.regime_market import market
    from oracle.store.bars import BarRepo

    n = 700
    days = [d for d in weekdays(n + 400, date(2023, 1, 2)) if d <= DAY][-n:]
    repo = BarRepo(ctx.conn)
    for s, bars in market(12, n, seed=5).items():
        repo.upsert([replace(b, symbol={"L000": "AAPL"}.get(s, s), date=d, currency="GBP")
                     for b, d in zip(bars, days)], "x")
    seed(ctx, "AAPL", "hold")
    watch(ctx, "AAPL")
    model = TiltModel({**dict.fromkeys(TILTED, 0.5), "ai_verdict": 1.5, "reason": "Cut everything."})
    tilted = step(ctx, model)
    rules = regime_step(ctx)
    assert len(model.asked) == 1 and tilted.mood == rules.mood
    a, r = regime_card(ctx, account="regime-ai").state, regime_card(ctx).state
    evident = set(a["recorded"]) | set(a["warnings"]) | set(a["good"])
    moved = {m for m, v in a["tilt"].items() if v == 0.5}
    assert moved and moved <= evident, "only methods with a sign or a record move"
    assert "ai_verdict" not in a["tilt"] and r["tilt"] == {}
    assert a["weights"]["ai_verdict"] > r["weights"]["ai_verdict"], "the untilted verdicts gain from the cuts"
    assert a["readings"] == r["readings"] and a["base_weights"] == r["base_weights"]
