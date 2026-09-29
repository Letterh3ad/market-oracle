"""regime-v2: the regime book with an unscored method's weight spread over the price methods
(not over every present method, which let the constant hold score pad the blend) and a
rotation out of stale holdings into clearly stronger candidates."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from oracle.analysts.indicators import YEAR
from oracle.broker.backtest import replay
from oracle.broker.orders import Action
from oracle.broker.regime import (METHODS, PRESETS, ROTATION, RegimeInputs, Rotation, regime_book, rotate,
                                  sector_cap, v2_settings)
from oracle.broker.regime.book import PRICE_METHODS
from oracle.broker.rules import BUY_LABELS, Lean, Position, decide
from tests.broker.regime_market import market, wild_after
from tests.broker.test_backtest import RISK, RULES, priced
from tests.broker.test_regime_book import SECTORS, book_of, days_of, inputs, settings

N = 420
R = Rotation()


@pytest.fixture(scope="module")
def history():
    return market(15, N)


def v2(**kw):
    return v2_settings(settings(**kw))


def lean(symbol, label="buy", probability=0.7):
    return Lean(symbol, date(2026, 1, 5), label, probability, "regime-v2")


def test_the_v2_settings_are_fixed_a_priori_and_v1_has_neither_change():
    assert (R.min_hold, R.margin, R.most) == (20, 0.25, 2)
    assert (settings().spread_unscored, settings().rotation) == (False, None)
    assert (v2().spread_unscored, v2().rotation) == (True, R)
    assert v2_settings(settings(), {"rotation_min_hold_days": 30, "rotation_margin": 0.3, "rotation_max": 1}
                       ).rotation == Rotation(30, 0.3, 1)


def test_an_unscored_methods_weight_goes_to_the_price_methods_present(history):
    book = book_of(history, inputs=inputs(history), settings=v2(), source="regime-v2")
    day = days_of(history)[-1]
    for symbol in ("L000", "L001", "L005"):
        e = book.explain(symbol, day)
        w = e.state.weights
        present = {m for m in e.scores if w[m] > 0}
        missing = sum(w.values()) - sum(w[m] for m in present)
        price = sum(w[m] for m in present & PRICE_METHODS)
        spread = {m: w[m] + (missing * w[m] / price if m in PRICE_METHODS else 0.0) for m in present}
        assert sum(spread.values()) == pytest.approx(1.0)
        assert e.parts == pytest.approx({m: spread[m] * e.scores[m] for m in present})
        assert sum(e.parts.values()) == pytest.approx(e.score)
        assert e.parts["hold"] == pytest.approx(w["hold"] * 0.3)
        assert e.lean.source == "regime-v2"
    padded = book_of(history, inputs=inputs(history)).explain("L005", day)
    assert padded.parts["hold"] > book.explain("L005", day).parts["hold"]


def test_a_listing_leans_only_on_price_evidence(history):
    young = [replace(b, symbol="NEW", date=d) for b, d in zip(market(1, 40, seed=11)["L000"], days_of(history)[-40:])]
    book = book_of({**history, "NEW": young}, settings=v2())
    assert book.explain("NEW", young[-1].date) is None
    no_price = {mood: {m: (0.0 if m in PRICE_METHODS else row[m]) for m in METHODS} for mood, row in PRESETS.items()}
    day = days_of(history)[-1]
    assert book_of(history, settings=v2(weights=no_price)).explain("L005", day) is None
    assert book_of(history, settings=settings(weights=no_price)).explain("L005", day) is not None


def test_a_stale_holding_well_under_the_best_candidate_is_sold_for_it():
    leans = {"H": lean("H", "hold"), "C": lean("C")}
    out = rotate(leans, {"H": date(2026, 1, 1)}, {"H": 0.05, "C": 0.30}, {"H": 20}, R)
    assert out["H"].label == "sell" and out["C"] == leans["C"]
    assert out["H"].note.startswith(ROTATION) and "make room for C" in out["H"].note
    [d] = [d for d in decide(out, {"H": Position("H", 10, 100.0, 100.0)}, 0.0, 1000.0, {}, RULES, set())
           if d.action == Action.SELL]
    assert d.reason == out["H"].note


@pytest.mark.parametrize(("days", "held", "best", "rotated"), [
    (19, 0.05, 0.30, False), (20, 0.05, 0.30, True), (60, 0.06, 0.30, False), (60, -0.2, 0.5, True)])
def test_rotation_needs_the_minimum_hold_and_the_margin(days, held, best, rotated):
    out = rotate({"H": lean("H", "hold"), "C": lean("C")}, {"H": date(2026, 1, 1)}, {"H": held, "C": best},
                 {"H": days}, R)
    assert (out["H"].label == "sell") is rotated


def test_at_most_two_a_step_weakest_first_each_for_its_own_candidate():
    leans = {**{h: lean(h, "hold") for h in ("H1", "H2", "H3")}, **{c: lean(c) for c in ("C1", "C2", "C3")}}
    held = dict.fromkeys(("H1", "H2", "H3"), date(2026, 1, 1))
    scores = {"H1": 0.0, "H2": -0.1, "H3": 0.05, "C1": 0.6, "C2": 0.5, "C3": 0.4}
    out = rotate(leans, held, scores, dict.fromkeys(held, 40), R)
    assert {s for s, x in out.items() if x.label == "sell"} == {"H1", "H2"}
    assert "make room for C1" in out["H2"].note and "make room for C2" in out["H1"].note
    scores = {**scores, "C2": 0.2, "C3": 0.2}
    out = rotate(leans, held, scores, dict.fromkeys(held, 40), R)
    assert {s for s, x in out.items() if x.label == "sell"} == {"H2"}


def test_no_rotation_into_a_pending_buy_or_out_of_one():
    leans = {"H": lean("H", "hold"), "P": lean("P"), "C": lean("C")}
    held = {"H": date(2026, 1, 1), "P": None}
    out = rotate(leans, held, {"H": 0.1, "P": 0.9, "C": 0.2}, {"H": 40}, R)
    assert out == leans
    out = rotate({"P": lean("P", "hold"), "C": lean("C")}, {"P": None}, {"P": -0.5, "C": 0.9}, {}, R)
    assert out["P"].label == "hold"


def test_a_holding_already_leaning_sell_or_without_a_score_is_left_to_the_rules():
    leans = {"S": lean("S", "sell"), "U": lean("U", "hold"), "C": lean("C")}
    out = rotate(leans, dict.fromkeys("SU", date(2026, 1, 1)), {"S": -0.5, "C": 0.9}, {"S": 40, "U": 40}, R)
    assert out == leans


def test_the_book_rotates_after_the_sector_cap(history):
    book = book_of(history, inputs=RegimeInputs(sectors=SECTORS), settings=v2(), source="regime-v2")
    day = days_of(history)[-1]
    explained = {s: e for s in history if (e := book.explain(s, day))}
    buys = sorted((e for e in explained.values() if e.lean.label in BUY_LABELS), key=lambda e: -e.score)
    assert len(buys) >= 2
    weakest = min((e for e in explained.values() if e.lean.label == "hold"), key=lambda e: e.score)
    assert buys[1].score - weakest.score >= 0.25
    full = [s for s in SECTORS if SECTORS[s] == SECTORS[buys[0].lean.symbol] and s != buys[0].lean.symbol
            and s != weakest.lean.symbol][:3]
    since = days_of(history)[-60]
    held = {weakest.lean.symbol: since, **dict.fromkeys(full, since)}
    leans = {s: e.lean for s, e in explained.items()}
    out = book.cap(leans, held)
    capped = sector_cap(leans, held, SECTORS, 3)
    assert capped[buys[0].lean.symbol].label == "hold"
    assert out[weakest.lean.symbol].label == "sell"
    best = max((e for s, e in explained.items() if capped[s].label in BUY_LABELS and s not in held),
               key=lambda e: e.score)
    assert f"make room for {best.lean.symbol}" in out[weakest.lean.symbol].note


def test_v1_never_rotates_whatever_the_hold_ages(history):
    book = book_of(history, inputs=RegimeInputs(sectors=SECTORS))
    day = days_of(history)[-1]
    leans = {s: e.lean for s in history if (e := book.explain(s, day))}
    old = dict.fromkeys(list(leans)[:5], days_of(history)[0])
    assert book.cap(leans, old) == book.cap(leans, set(old)) == sector_cap(leans, set(old), SECTORS, 3)


def test_rotation_reads_nothing_after_the_day(history):
    days = days_of(history)
    cut = days[N - 40]
    early_rows = {s: priced([b for b in bars if b.date <= cut]) for s, bars in history.items()}
    wild_rows = {s: priced(bars) for s, bars in wild_after(history, cut).items()}
    runs = []
    for rows in (early_rows, wild_rows):
        book = regime_book(rows, settings=v2(), inputs=RegimeInputs(sectors=SECTORS), source="regime-v2")
        runs.append(replay(rows, days[YEAR + 1], cut, rules=RULES, risk=RISK, slippage=0.001, commission=0.0,
                           lean=book.lean_source(), cap=book.cap))
    assert runs[0].trades == runs[1].trades and runs[0].orders == runs[1].orders
    assert any(d.reason.startswith(ROTATION) for _, d in runs[0].orders)


def test_the_replay_rotates_only_holdings_held_long_enough(history):
    rows = {s: priced(bars) for s, bars in history.items()}
    book = regime_book(rows, settings=v2(), inputs=RegimeInputs(sectors=SECTORS), source="regime-v2")
    days = days_of(history)
    r = replay(rows, days[YEAR + 1], days[-1], rules=RULES, risk=RISK, slippage=0.001, commission=0.0,
               lean=book.lean_source(), cap=book.cap)
    bought: dict[str, date] = {}
    rotated = 0
    for t in r.trades:
        if t.side.value == "buy":
            bought.setdefault(t.symbol, t.day)
            continue
        if t.reason.startswith(ROTATION):
            rotated += 1
            placed = max(d for d, o in r.orders if o.symbol == t.symbol and d < t.day)
            assert sum(1 for d in days if bought[t.symbol] < d <= placed) >= R.min_hold
        bought.pop(t.symbol)
    assert rotated
    per_day: dict[date, int] = {}
    for d, o in r.orders:
        if o.reason.startswith(ROTATION):
            per_day[d] = per_day.get(d, 0) + 1
    assert max(per_day.values()) <= R.most


def test_the_replay_gives_the_cap_each_holdings_first_fill_day_and_pending_buys_none():
    seen = []

    def cap(leans, held):
        seen.append(dict(held))
        return leans

    bars = market(2, 300)
    gap = bars["L001"][261].date
    rows = {"L000": priced(bars["L000"]), "L001": priced([b for b in bars["L001"] if b.date != gap])}
    buy = lambda s, b: Lean(s, b[-1].date, "buy", 0.9, "t")  # noqa: E731
    r = replay(rows, bars["L000"][260].date, bars["L000"][-1].date, rules=RULES, risk=RISK, slippage=0.0,
               commission=0.0, lean=buy, cap=cap)
    first = {"L000": bars["L000"][261].date, "L001": bars["L001"][262].date}
    assert [(t.symbol, t.day) for t in r.trades[:2]] == list(first.items())
    assert seen[:3] == [{}, {"L000": first["L000"], "L001": None}, first]
