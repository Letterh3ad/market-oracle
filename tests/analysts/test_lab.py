"""The PASS bar against hand values, and the deflated Sharpe on simulated null strategies."""

import math
import random

import pytest

from oracle.analysts.lab import (
    Moments, bonferroni_bar, deflated_sharpe, expected_max_sharpe, moments, probabilistic_sharpe,
)


@pytest.mark.parametrize("n, bar", [
    (1, 3.0),      # 1.9600: the floor
    (10, 3.0),     # z(1 - 0.05/20) = 2.8070: the floor
    (20, 3.0233),  # z(1 - 0.05/40)
    (100, 3.4808),  # z(1 - 0.05/200)
])
def test_the_bar_is_bonferroni_two_sided_never_under_three(n, bar):
    assert bonferroni_bar(n) == pytest.approx(bar, abs=1e-4)


def test_the_bar_needs_a_test():
    with pytest.raises(ValueError):
        bonferroni_bar(0)


def test_moments_of_a_known_series():
    m = moments([1.0, -1.0, 1.0, -1.0])
    assert (m.sharpe, m.n, m.skew, m.kurtosis) == (0.0, 4, 0.0, 1.0)
    assert moments([0.01, 0.01, 0.01]) is None
    assert moments([0.01, 0.02]) is None


def test_the_expected_best_of_many_null_tries_is_close_to_the_normal_order_statistic():
    # E[max of 100 standard normals] = 2.5076; the theorem's closed form is within 0.03.
    assert expected_max_sharpe(100, 1.0) == pytest.approx(2.5076, abs=0.03)
    assert expected_max_sharpe(1, 1.0) == 0.0
    assert expected_max_sharpe(10, 4.0) == pytest.approx(2 * expected_max_sharpe(10, 1.0))


def test_with_one_variant_the_deflated_sharpe_is_the_probabilistic_sharpe_against_zero():
    m = Moments(0.05, 1000, -0.5, 6.0)
    assert deflated_sharpe(m, 1) == probabilistic_sharpe(m, 0.0)
    assert 0.5 < deflated_sharpe(m, 1) < 1
    assert deflated_sharpe(m, 50) < deflated_sharpe(m, 5) < deflated_sharpe(m, 1)


def _null(rng: random.Random, days: int) -> list[float]:
    return [rng.gauss(0, 0.01) for _ in range(days)]


def test_on_a_simulated_null_the_best_of_many_random_strategies_rarely_reaches_095():
    """Each trial tries 20 skill-free strategies and keeps the best: its plain probabilistic
    Sharpe is nearly always >= 0.95, its deflated Sharpe about as often as the 5% it promises."""
    rng, trials, variants, days = random.Random(7), 150, 20, 250
    plain = deflated = 0
    for _ in range(trials):
        best = max((moments(_null(rng, days)) for _ in range(variants)), key=lambda m: m.sharpe)
        plain += probabilistic_sharpe(best, 0.0) >= 0.95
        deflated += deflated_sharpe(best, variants) >= 0.95
    assert plain / trials > 0.5
    assert deflated / trials <= 0.10


def test_one_random_strategy_tried_once_passes_about_one_time_in_twenty():
    rng, trials = random.Random(11), 600
    hits = sum(deflated_sharpe(moments(_null(rng, 250)), 1) >= 0.95 for _ in range(trials))
    assert hits / trials <= 0.09


def test_a_real_edge_survives_the_deflation():
    rng = random.Random(3)
    m = moments([rng.gauss(0.002, 0.01) for _ in range(1000)])  # daily Sharpe 0.2, about 3.2 a year
    assert deflated_sharpe(m, 50) > 0.99
    assert math.isclose(deflated_sharpe(m, 50, variance=0.0), deflated_sharpe(m, 1))
