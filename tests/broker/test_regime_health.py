from dataclasses import replace
from datetime import timedelta

import pytest

from oracle.analysts.indicators import YEAR
from oracle.broker.regime import METHODS, PRESETS, RegimeInputs
from oracle.broker.regime.book import NO_WEIGHT
from oracle.broker.regime.playbook import HORIZON, RECORD, health, top_fifth
from tests.broker.regime_market import market, walk
from tests.broker.test_regime_book import book_of, days_of, normalised, raw_weights, settings

N = 420


@pytest.fixture(scope="module")
def history():
    return market(15, N)


def test_health_is_each_methods_top_fifth_over_the_composite_on_completed_windows(history):
    book = book_of(history)
    days = days_of(history)
    closes = {s: [b.close for b in bars] for s, bars in history.items()}
    level = [1.0]
    for i in range(1, N):
        level.append(level[-1] * (1 + sum(c[i] / c[i - 1] - 1 for c in closes.values()) / len(closes)))
    for i in (N - 1, N - 45):
        expected = {}
        for m in METHODS:
            record = []
            for k in range(i - HORIZON - RECORD + 1, i - HORIZON + 1):
                picks = top_fifth({s: v[m] for s in history if m in (v := book.scores(s, days[k]))})
                if picks:
                    mean = sum(closes[s][k + HORIZON] / closes[s][k] for s in picks) / len(picks)
                    record.append(mean - level[k + HORIZON] / level[k])
            expected[m] = health(record)
        assert book.state(days[i]).health == pytest.approx(expected)
        assert expected["hold"] == 1.0 and expected["ai_verdict"] == 1.0 and expected["trend"] != 1.0


def trending(n=N):
    """Ten listings: L000 and L001 climb, the rest drift on the market."""
    base = market(10, n, seed=3)
    for s in ("L000", "L001"):
        bars = base[s]
        base[s] = walk(s, [0.003 + (b.close / a.close - 1) for a, b in zip(bars, bars[1:])])
    return base


def verdicts_for(history, favoured):
    first = history["L000"][0].date
    return {s: [(first + timedelta(days=d), 1.0 if s in favoured else -1.0) for d in range(0, 700, 5)]
            for s in history}


def test_health_follows_the_methods_record():
    history = trending()
    last = days_of(history)[-1]
    good = book_of(history, inputs=RegimeInputs(verdicts=verdicts_for(history, {"L000", "L001"}))).state(last)
    bad = book_of(history, inputs=RegimeInputs(verdicts=verdicts_for(history, {"L002", "L003"}))).state(last)
    assert good.health["ai_verdict"] == 1.5
    assert bad.health["ai_verdict"] < 0.9
    assert good.weights["ai_verdict"] > bad.weights["ai_verdict"]


def test_health_is_neutral_until_the_record_is_long_enough(history):
    book = book_of(history)
    first = next(d for d in days_of(history) if book.state(d))
    assert book.state(first).health["trend"] == 1.0


def test_a_listing_whose_last_bar_is_over_ten_days_old_does_not_lean(history):
    days = days_of(history)
    stopped = {**history, "L000": [b for b in history["L000"] if b.date <= days[-30]]}
    book = book_of(stopped)
    assert book.explain("L000", days[-30] + timedelta(days=10)) is not None
    assert book.explain("L000", days[-30] + timedelta(days=11)) is None
    assert book.scores("L000", days[-1]) == {}
    assert book.explain("L001", days[-1]) is not None


def test_a_method_at_zero_drops_out_of_every_weight_and_lean(history):
    weights = {mood: {**row, "trend": 0.0} for mood, row in PRESETS.items()}
    book = book_of(history, settings=settings(weights=weights))
    for day in days_of(history)[YEAR + 1::15]:
        assert book.state(day).weights["trend"] == 0.0
        e = book.explain("L003", day)
        assert "trend" in e.scores and "trend" not in e.parts
        assert sum(e.parts.values()) == pytest.approx(e.score)


def test_with_every_weight_at_zero_nothing_leans_and_the_smoothing_carries_on(history):
    days = days_of(history)
    off = days[N - 30]
    book = book_of(history, settings=settings(tilt_cap=1.0),
                   tilt=lambda s: dict.fromkeys(METHODS, 0.0) if s.day == off else {})
    state = book.state(off)
    assert state.note == NO_WEIGHT and set(state.weights.values()) == {0.0}
    assert all(book.explain(s, off) is None for s in history)
    before, after = book.state(days[N - 31]), book.state(days[N - 29])
    assert after.note is None
    a = 2 / 6
    today = normalised(raw_weights(after, settings(tilt_cap=1.0)))
    assert after.weights == pytest.approx({m: a * today[m] + (1 - a) * before.weights[m] for m in METHODS})


def test_momentum_warns_when_its_picks_crowd_into_one_sector():
    history = market(20, N, seed=5)
    days = days_of(history)[YEAR + 40:]
    crowded = book_of(history, inputs=RegimeInputs(sectors=dict.fromkeys(history, "Tech")))
    spread = book_of(history)
    assert all("sector_crowded" in crowded.state(d).warnings["momentum"] for d in days[::10])
    assert not any("sector_crowded" in spread.state(d).warnings.get("momentum", ()) for d in days)


def test_a_partial_last_session_changes_no_state_or_lean(history):
    days = days_of(history)
    extra = days[-1] + timedelta(days=3)
    partial = {**history, "L000": [*history["L000"], replace(history["L000"][-1], date=extra, close=500.0,
                                                               adj_close=500.0)]}
    full, thin = book_of(history), book_of(partial)
    assert thin.state(extra) == full.state(days[-1])
    for s in ("L001", "L005"):
        assert thin.explain(s, days[-1]) == full.explain(s, days[-1])
