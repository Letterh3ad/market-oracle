from datetime import date, timedelta

import pytest

from oracle.analysts.price_factors import PriceFactors, Ranked
from oracle.broker.regime import methods
from oracle.broker.regime.methods import AVERAGE, DRIFT_DAYS, SLOPE_DAYS, listing, with_reactions
from tests.broker.regime_market import walk

SETTLED = AVERAGE + SLOPE_DAYS


def of(returns, symbol="X"):
    return listing(symbol, walk(symbol, returns))


def wobble(n, daily, size=0.004):
    return [daily + size * (-1) ** i for i in range(n)]


def test_trend_scores_a_rising_listing_up_and_a_falling_one_down():
    up, down = of(wobble(300, 0.002)), of(wobble(300, -0.002))
    assert methods.trend(up, 299) > 0.5
    assert methods.trend(down, 299) < -0.5
    assert -1 <= methods.trend(down, 299) and methods.trend(up, 299) <= 1


def test_trend_has_no_score_before_the_average_and_its_slope_settle():
    x = of(wobble(300, 0.002))
    assert methods.trend(x, SETTLED - 2) is None
    assert methods.trend(x, SETTLED - 1) is not None


def test_trend_is_halved_when_the_average_slopes_against_the_gap():
    # A long climb, then a drop under the still-rising average.
    x = of(wobble(260, 0.002) + [-0.04] * 6)
    i = len(x.dates) - 1
    gap = x.closes[i] / x.average[i] - 1
    assert gap < 0 < x.slope[i]
    assert methods.trend(x, i) == pytest.approx(max(-1.0, gap / methods.TREND_SCALE) * 0.5)


def test_a_price_break_in_the_window_leaves_no_trend_or_reversion_score():
    returns = wobble(450, 0.001)
    returns[200] = -0.6  # a split the stored bars were not re-based for
    x = of(returns)
    assert methods.trend(x, 400) is None and methods.reversion(x, 400) is None
    assert methods.trend(x, 201 + SETTLED - 1) is not None


def test_reversion_buys_a_sharp_dip_in_an_uptrend():
    x = of(wobble(260, 0.003, 0.002) + [-0.02, -0.02])
    i = len(x.dates) - 1
    assert x.rsi2[i] < 10 and x.closes[i] > x.average[i]
    assert methods.reversion(x, i) == 1.0


def test_reversion_leans_against_a_spike_and_is_flat_otherwise():
    spike = of(wobble(260, 0.001, 0.002) + [0.03, 0.03])
    assert methods.reversion(spike, len(spike.dates) - 1) == -0.5
    calm = of(wobble(262, 0.001))
    assert methods.reversion(calm, 261) == 0.0
    assert methods.reversion(calm, SETTLED - 2) is None


def test_a_dip_below_the_average_is_not_bought():
    x = of(wobble(260, -0.002, 0.002) + [-0.03, -0.03])
    i = len(x.dates) - 1
    assert x.rsi2[i] < 10 and x.closes[i] < x.average[i]
    assert methods.reversion(x, i) == 0.0


def drift_listing(before_open, market=0.01):
    x = of([0.0] * 100 + [0.07] + [0.0] * 80)
    release = x.dates[101] if before_open else x.dates[100]
    return with_reactions(x, [(release, before_open)], lambda d: market)


@pytest.mark.parametrize("before_open", [True, False, None])
def test_earnings_drift_follows_the_reaction_over_the_market_and_fades(before_open):
    x = drift_listing(before_open)
    assert x.reactions == [(101, pytest.approx(0.06))]
    assert methods.earnings_drift(x, 100) is None
    assert methods.earnings_drift(x, 101) == 1.0
    assert methods.earnings_drift(x, 101 + 30) == pytest.approx(0.5)
    assert methods.earnings_drift(x, 101 + DRIFT_DAYS) is None


def test_earnings_drift_scales_a_small_reaction_and_needs_a_market_return():
    small = drift_listing(True, market=0.045)
    assert methods.earnings_drift(small, 101) == pytest.approx(0.025 / methods.DRIFT_SCALE)
    x = of([0.0] * 20)
    assert with_reactions(x, [(x.dates[5], True)], lambda d: None).reactions == []
    assert methods.earnings_drift(x, 10) is None


def test_a_release_with_no_reaction_bar_near_it_is_skipped():
    x = of([0.0] * 20)
    assert with_reactions(x, [(x.dates[-1] + timedelta(days=1), False)], lambda d: 0.0).reactions == []
    assert with_reactions(x, [(x.dates[0] - timedelta(days=30), True)], lambda d: 0.0).reactions == []


def ranked(momentum_pct, low_vol_pct):
    return Ranked(PriceFactors("X", date(2026, 1, 1), None, None), momentum_pct, low_vol_pct)


def test_momentum_and_low_vol_map_percentiles_onto_scores():
    assert methods.momentum(ranked(1.0, 0.25)) == 1.0
    assert methods.low_vol(ranked(1.0, 0.25)) == -0.5
    assert methods.momentum(ranked(None, 0.5)) is None
    assert methods.low_vol(ranked(0.5, None)) is None
    assert methods.momentum(None) is None and methods.low_vol(None) is None


def test_dated_scores_read_the_newest_known_on_the_day_and_go_stale():
    scores = [(date(2026, 1, 1), 0.4), (date(2026, 3, 1), -2.0)]
    age = timedelta(days=7)
    assert methods.dated(scores, date(2025, 12, 31), age) is None
    assert methods.dated(scores, date(2026, 1, 5), age) == 0.4
    assert methods.dated(scores, date(2026, 1, 9), age) is None
    assert methods.dated(scores, date(2026, 3, 1), age) == -1.0
    assert methods.dated([], date(2026, 3, 1), age) is None


def test_scores_on_a_bar_do_not_change_when_later_bars_arrive():
    base = wobble(300, 0.002, 0.01)
    early, late = of(base), of(base + [-0.4, 2.0, 0.5] * 10)
    for i in range(len(early.dates)):
        assert (methods.trend(early, i), methods.reversion(early, i)) == (methods.trend(late, i), methods.reversion(late, i))
