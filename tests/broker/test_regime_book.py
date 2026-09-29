from collections import defaultdict
from dataclasses import replace
from datetime import timedelta

import pytest

from oracle.analysts.indicators import YEAR
from oracle.broker.backtest import replay
from oracle.broker.orders import Side
from oracle.broker.regime import (METHODS, PRESETS, RegimeInputs, RegimeSettings, regime_book, sector_cap,
                                  settings_from)
from oracle.broker.regime.book import NO_WEIGHT
from oracle.broker.regime.playbook import Day, firing
from oracle.broker.rules import BUY_LABELS, Lean
from tests.broker.regime_market import market, wild_after
from tests.broker.test_backtest import RISK, RULES, priced

N = 420
SECTORS = {f"L{k:03d}": ("Tech", "Energy", "Banks")[k % 3] for k in range(15)}


@pytest.fixture(scope="module")
def history():
    return market(15, N)


def inputs(history, cut=None):
    """Verdicts, factor scores and earnings for a few listings; those after `cut` left out."""
    days = history["L000"][0].date, history["L000"][-1].date
    keep = (lambda d: d <= cut) if cut else (lambda d: True)
    first = days[0]
    verdicts = {"L000": [(first + timedelta(days=d), 0.5) for d in range(0, 700, 5) if keep(first + timedelta(days=d))]}
    factors = {"L001": [(first + timedelta(days=d), -0.4) for d in (300, 450, 560) if keep(first + timedelta(days=d))]}
    earnings = {"L002": [(first + timedelta(days=d), False) for d in (380, 470, 555) if keep(first + timedelta(days=d))]}
    return RegimeInputs(sectors=SECTORS, earnings=earnings, factors=factors, verdicts=verdicts)


def settings(**kw):
    return RegimeSettings(**{"min_probability": 0.6, **kw})


def book_of(history, **kw):
    return regime_book(history, **{"settings": settings(), **kw})


def raw_weights(state, s=None):
    s = s or settings()
    return {m: state.base_weights[m] * min(s.playbook_cap, s.warning_factor ** len(state.warnings.get(m, ()))
                                           * s.good_factor ** len(state.good.get(m, ())))
            * state.health[m] * state.tilt.get(m, 1.0) for m in METHODS}


def normalised(raw):
    return {m: v / sum(raw.values()) for m, v in raw.items()}


def days_of(history):
    return [b.date for b in history["L000"]]


def test_no_state_or_lean_under_a_year_of_history(history):
    book = book_of(history)
    day = days_of(history)[YEAR - 1]
    assert book.state(day) is None
    assert book.explain("L000", day) is None
    assert book.lean_source()("L000", [b for b in history["L000"] if b.date <= day]) is None
    assert book.state(days_of(history)[0] - timedelta(days=1)) is None


def test_a_state_carries_the_moods_preset_its_signs_and_the_records(history):
    book = book_of(history)
    state = book.state(days_of(history)[-1])
    assert state.mood in PRESETS
    assert state.base_weights == pytest.approx(PRESETS[state.mood])
    assert sum(state.weights.values()) == pytest.approx(1.0)
    assert set(state.health) == set(METHODS) and all(0.5 <= v <= 1.5 for v in state.health.values())
    assert state.health["hold"] == 1.0 and len(set(state.health.values())) > 1
    assert (state.tilt, state.note) == ({}, None)
    assert set(state.readings) >= {"trend_gap", "vol_pct", "ret_12m", "ret_1m", "corr_pct"}
    signs = [(s.warnings, s.good) for d in days_of(history)[YEAR:] if (s := book.state(d))]
    assert any(w or g for w, g in signs)


def test_with_a_one_day_span_the_weights_are_preset_times_playbook_times_health(history):
    book = book_of(history, settings=settings(span=1))
    for day in days_of(history)[YEAR + 5::20]:
        state = book.state(day)
        assert state.weights == pytest.approx(normalised(raw_weights(state)))


def test_the_signs_on_a_day_are_the_playbooks_for_its_mood(history):
    book = book_of(history, inputs=RegimeInputs(sectors=SECTORS))
    for day in days_of(history)[YEAR + 1::10]:
        state = book.state(day)
        mood = book._composite.mood(day)
        picks = book._picks.get(book._composite.index(day), {})
        assert (state.warnings, state.good) == firing(Day(mood, picks, SECTORS))


def test_weights_are_smoothed_over_the_span(history):
    days = days_of(history)
    switch = days[N - 40]
    book = book_of(history, tilt=lambda s: {"trend": 1.5} if s.day >= switch else {})
    before, state = book.state(days[N - 41]), book.state(switch)
    raw = normalised(raw_weights(state))
    a = 2 / (5 + 1)
    assert state.weights == pytest.approx({m: a * raw[m] + (1 - a) * before.weights[m] for m in METHODS})
    assert sum(state.weights.values()) == pytest.approx(1.0)


def test_the_tilt_is_capped_and_unknown_methods_are_ignored(history):
    book = book_of(history, tilt=lambda s: {"trend": 9.0, "hold": -3, "astrology": 2.0})
    state = book.state(days_of(history)[-1])
    assert state.tilt == {"trend": 1.5, "hold": 0.5}
    assert book_of(history).state(days_of(history)[-1]).weights["trend"] < state.weights["trend"]


def test_explain_blends_the_present_methods_renormalised(history):
    book = book_of(history, inputs=inputs(history))
    day = days_of(history)[-1]
    for symbol in ("L000", "L001", "L005"):
        e = book.explain(symbol, day)
        used = {m: e.state.weights[m] for m in e.scores if e.state.weights[m] > 0}
        assert e.parts == pytest.approx({m: w * e.scores[m] / sum(used.values()) for m, w in used.items()})
        assert sum(e.parts.values()) == pytest.approx(e.score)
        assert e.lean.symbol == symbol and e.lean.as_of == day and e.lean.source == "regime"
    assert "ai_verdict" in book.explain("L000", day).scores and "ai_verdict" not in book.explain("L005", day).scores
    assert book.explain("L001", day).scores["quality_value"] == -0.4
    assert "quality_value" not in book.explain("L000", day).scores
    assert book.explain("L005", day).scores["hold"] == 0.3


def test_a_listing_with_no_price_method_score_does_not_lean(history):
    young = market(1, 40, seed=11)["L000"]
    young = [replace(b, symbol="NEW") for b in young]
    young = [replace(b, date=d) for b, d in zip(young, days_of(history)[-40:])]
    book = book_of({**history, "NEW": young})
    assert book.scores("NEW", young[-1].date) == {}
    assert book.explain("NEW", young[-1].date) is None
    assert book.explain("NOPE", young[-1].date) is None


def test_buys_clear_the_rules_threshold_and_the_strongest_is_likeliest(history):
    book = book_of(history, settings=settings(min_probability=0.7))
    explained = [e for s in history for d in days_of(history)[YEAR + 1::7] if (e := book.explain(s, d))]
    buys = [e for e in explained if e.lean.label in BUY_LABELS]
    assert buys and all(e.lean.probability >= 0.7 for e in buys)
    by_score = sorted(buys, key=lambda e: e.score)
    assert [e.lean.probability for e in by_score] == sorted(e.lean.probability for e in buys)
    labels = {e.lean.label for e in explained}
    assert "hold" in labels and labels <= {"strong sell", "sell", "hold", "buy", "strong buy"}


def lean(symbol, probability, label="buy"):
    return Lean(symbol, None, label, probability, "regime")


def test_the_sector_cap_keeps_the_likeliest_buys_and_counts_holdings():
    sectors = {"A": "Tech", "B": "Tech", "C": "Tech", "D": "Energy", "H": "Tech"}
    leans = {"A": lean("A", 0.9), "B": lean("B", 0.8), "C": lean("C", 0.95), "D": lean("D", 0.7),
             "E": lean("E", 0.6), "H": lean("H", 0.9), "S": lean("S", 0.9, "sell")}
    out = sector_cap(leans, {"H"}, sectors, 2)
    assert {s: x.label for s, x in out.items()} == {"A": "hold", "B": "hold", "C": "buy", "D": "buy", "E": "buy",
                                                    "H": "buy", "S": "sell"}
    assert out["A"].probability == 0.9
    assert sector_cap(leans, {"H", "X"}, {**sectors, "X": "Tech"}, 2)["C"].label == "hold"


def test_the_book_caps_at_thirty_percent_of_the_positions_rounded_up(history):
    book = book_of(history, inputs=RegimeInputs(sectors={s: "Tech" for s in "ABCDE"}))
    leans = {s: lean(s, 0.9 - i / 100) for i, s in enumerate("ABCDE")}
    assert [x.label for x in book.cap(leans, set()).values()] == ["buy", "buy", "buy", "hold", "hold"]
    book = book_of(history, inputs=RegimeInputs(sectors={s: "Tech" for s in "ABCDE"}),
                   settings=settings(sector_share=0.21))
    assert [x.label for x in book.cap(leans, set()).values()] == ["buy", "buy", "buy", "hold", "hold"]


def test_nothing_on_or_before_a_day_changes_when_wild_bars_follow_it(history):
    days = days_of(history)
    cut = days[N - 60]
    early = book_of({s: [b for b in bars if b.date <= cut] for s, bars in history.items()},
                    inputs=inputs(history, cut))
    wild = wild_after(history, cut)
    late = book_of(wild, inputs=inputs(wild))
    for day in [d for d in days if d <= cut][YEAR - 5::3] + [cut]:
        assert late.state(day) == early.state(day)
        for s in history:
            assert late.explain(s, day) == early.explain(s, day)
            bars = [b for b in wild[s] if b.date <= day]
            assert late.lean_source()(s, bars) == early.lean_source()(s, bars)
    assert len(set(early.state(cut).health.values())) > 1


def test_the_replay_runs_the_book_under_the_sector_cap(history):
    rows = {s: priced(bars) for s, bars in history.items()}
    book = book_of(rows, inputs=RegimeInputs(sectors=SECTORS))
    days = days_of(history)
    r = replay(rows, days[YEAR + 1], days[-1], rules=RULES, risk=RISK, slippage=0.001, commission=0.0,
               lean=book.lean_source(), cap=book.cap)
    assert r.trades and {t.side for t in r.trades} == {Side.BUY, Side.SELL}
    held = defaultdict(set)
    for t in sorted(r.trades, key=lambda t: (t.day, t.side != Side.SELL)):
        if t.side == Side.BUY:
            held[SECTORS[t.symbol]].add(t.symbol)
        else:
            held[SECTORS[t.symbol]].discard(t.symbol)
        assert all(len(v) <= 3 for v in held.values())


def test_priced_history_puts_the_composite_in_the_base_currency(history):
    bars = history["L000"]
    switch = bars[200].date
    local = book_of({"L000": priced(bars)})
    base = book_of({"L000": priced(bars, lambda d: 1.0 if d < switch else 1.5)})
    assert base._composite.return_on(switch) == pytest.approx((1 + local._composite.return_on(switch)) / 1.5 - 1)


def test_settings_come_from_the_flat_config_keys():
    s = settings_from({"calm_up_trend": 0.5, "smoothing_span": 3, "sector_share": 0.2, "tilt_cap": 0.25},
                      max_positions=20, min_probability=0.65)
    assert s.weights["calm_up"]["trend"] == 0.5 and s.weights["calm_up"]["momentum"] == PRESETS["calm_up"]["momentum"]
    assert (s.span, s.sector_share, s.tilt_cap, s.max_positions, s.min_probability) == (3, 0.2, 0.25, 20, 0.65)
    assert settings_from({}, max_positions=10, min_probability=0.6) == settings(
        weights={m: dict(r) for m, r in PRESETS.items()})


def test_the_buy_threshold_is_always_given():
    with pytest.raises(TypeError):
        RegimeSettings()
