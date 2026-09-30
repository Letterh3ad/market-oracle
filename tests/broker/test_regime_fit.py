from datetime import date, timedelta

import pytest

from oracle.analysts.indicators import YEAR
from oracle.broker.regime import METHODS, MOODS, PRESETS, regime_book
from oracle.broker.regime.fit import BLEND, MIN_WINDOWS, TRAILING, Window, fit_presets
from oracle.broker.regime.playbook import HORIZON
from tests.broker.regime_market import market, wild_after
from tests.broker.test_regime_book import settings

DAY = date(2026, 3, 2)


def windows(mood, method, excess, n=MIN_WINDOWS, closed=DAY - timedelta(days=1), picked=None):
    picked = picked or closed - timedelta(days=28)
    return [Window(picked, closed, mood, method, excess)] * n


def preset(mood):
    total = sum(PRESETS[mood].values())
    return {m: v / total for m, v in PRESETS[mood].items()}


def test_an_unseen_mood_keeps_its_preset():
    fitted = fit_presets([], PRESETS, DAY)
    for mood in MOODS:
        assert fitted[mood] == pytest.approx(preset(mood))


def test_the_recorded_methods_share_their_preset_mass_by_positive_mean_excess_blended_half_and_half():
    found = (windows("calm_up", "trend", 0.03) + windows("calm_up", "momentum", 0.01)
             + windows("calm_up", "low_vol", -0.02))
    fitted = fit_presets(found, PRESETS, DAY)["calm_up"]
    p = preset("calm_up")
    mass = p["trend"] + p["momentum"] + p["low_vol"]
    learned = {**p, "trend": mass * 0.75, "momentum": mass * 0.25, "low_vol": 0.0}
    assert fitted == pytest.approx({m: BLEND * p[m] + (1 - BLEND) * learned[m] for m in METHODS})
    assert sum(fitted.values()) == pytest.approx(1.0)
    assert fitted["low_vol"] == pytest.approx(BLEND * p["low_vol"])
    assert fitted["hold"] == pytest.approx(p["hold"]) and fitted["reversion"] == pytest.approx(p["reversion"])


def test_weights_are_bounded_and_sum_to_one_whatever_the_record():
    found = [w for mood in MOODS for k, m in enumerate(METHODS)
             for w in windows(mood, m, (-1) ** k * 0.01 * (k + 1))]
    for mood, row in fit_presets(found, PRESETS, DAY).items():
        assert sum(row.values()) == pytest.approx(1.0)
        assert all(0.0 <= v <= 1.0 for v in row.values())
        assert all(row[m] >= BLEND * preset(mood)[m] - 1e-12 for m in METHODS)


def test_no_method_ahead_of_the_composite_learns_nothing():
    found = windows("volatile_down", "trend", -0.01) + windows("volatile_down", "low_vol", 0.0)
    assert fit_presets(found, PRESETS, DAY)["volatile_down"] == pytest.approx(preset("volatile_down"))


def test_a_method_with_too_few_windows_keeps_its_preset_share():
    found = windows("calm_up", "trend", 0.05, n=MIN_WINDOWS - 1) + windows("calm_up", "momentum", 0.01)
    fitted = fit_presets(found, PRESETS, DAY)["calm_up"]
    p = preset("calm_up")
    assert fitted["trend"] == pytest.approx(p["trend"]) and fitted["momentum"] == pytest.approx(p["momentum"])


def test_only_windows_picked_in_the_trailing_two_years_and_closed_before_the_day_count():
    base = windows("calm_up", "momentum", 0.01)
    old = windows("calm_up", "trend", 0.05, picked=DAY - TRAILING - timedelta(days=1))
    unfinished = windows("calm_up", "trend", 0.05, closed=DAY)
    assert fit_presets(base + old + unfinished, PRESETS, DAY)["calm_up"] == pytest.approx(preset("calm_up"))
    inside = windows("calm_up", "trend", 0.05, picked=DAY - TRAILING)
    assert fit_presets(base + inside, PRESETS, DAY)["calm_up"] != pytest.approx(preset("calm_up"))


N = 900


@pytest.fixture(scope="module")
def history():
    return market(15, N)


def days_of(history):
    return [b.date for b in history["L000"]]


def test_the_book_records_every_completed_pick_window_with_its_mood(history):
    book = regime_book(history, settings=settings())
    day = days_of(history)[-1]
    found = book.windows(day)
    assert found and {w.method for w in found} >= {"trend", "momentum", "low_vol"}
    assert {w.mood for w in found} <= set(MOODS) and all(w.closed <= day for w in found)
    days = days_of(history)
    assert all(days.index(w.closed) - days.index(w.day) == HORIZON for w in found)
    assert "hold" not in {w.method for w in found}


def test_a_refit_book_trades_each_segment_on_the_presets_fitted_at_its_start(history):
    days = days_of(history)
    refits = [days[YEAR + 200], days[YEAR + 330]]
    book = regime_book(history, settings=settings(), refits=refits)
    plain = regime_book(history, settings=settings())
    assert [d for d, _ in book.fits] == []
    book.state(days[-1])
    assert [d for d, _ in book.fits] == refits
    for (d, fitted), later in zip(book.fits, [refits[1], days[-1] + timedelta(days=1)]):
        assert fitted == fit_presets(plain.windows(d), settings().weights, d)
        for day in (x for x in days if d <= x < later):
            state = book.state(day)
            assert state.base_weights == pytest.approx(fitted[state.mood])
    assert fitted != {m: preset(m) for m in MOODS}
    before = book.state(days[YEAR + 199])
    assert before.base_weights == pytest.approx(preset(before.mood))


def test_a_fit_never_reads_bars_on_or_after_its_day(history):
    days = days_of(history)
    day = days[YEAR + 330]
    cut = days[YEAR + 329]
    wild = wild_after(history, cut)
    fits = [regime_book(h, settings=settings(), refits=[day]) for h in (history, wild)]
    for b in fits:
        b.state(day)
    assert fits[0].fits == fits[1].fits
    assert fits[0].fits[0][1] != {m: preset(m) for m in MOODS}
