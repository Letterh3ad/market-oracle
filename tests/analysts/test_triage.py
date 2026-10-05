"""The funnel's stage numbers (research-lab 05): monthly long-short spans, the publication split,
the literature's effect as an IC, and the testbed's power checked against next-signals' recorded
values."""

import math

import pytest

from oracle.analysts.signal_test import power
from oracle.analysts.triage import (
    detectable_ic, detectable_excess, ic_from_spread, publication_split, span,
)

RETS = {"1999-11": 1.0, "1999-12": 3.0, "2000-01": 2.0, "2000-02": -1.0, "2000-03": 4.0, "2001-01": 1.0}


def test_a_span_is_the_mean_t_and_sharpe_of_its_months():
    s = span("post-2000", RETS, "2000-01", None)
    vals = [2.0, -1.0, 4.0, 1.0]
    mean = sum(vals) / 4
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / 3)
    assert (s.n, s.start, s.end) == (4, "2000-01", "2001-01")
    assert s.mean == pytest.approx(mean) and s.sd == pytest.approx(sd)
    assert s.t == pytest.approx(mean / (sd / 2)) and s.sharpe == pytest.approx(mean / sd * math.sqrt(12))


def test_a_span_with_under_two_months_has_no_statistics():
    s = span("x", RETS, "2001-01", "2001-12")
    assert s.n == 1 and s.mean == 1.0 and s.t is None and s.sharpe is None
    assert span("x", RETS, "2030-01", None).mean is None


def test_the_publication_split_follows_mclean_and_pontiff():
    """In sample (the paper's years), out of sample before publication, after publication (McLean and
    Pontiff 2016, JF 71(1)); the decay is the share of the in-sample mean lost after publication."""
    rets = {f"{y}-{m:02d}": (2.0 if y <= 1990 else 1.0 if y <= 1993 else 0.5) + (0.1 if m % 2 else -0.1)
            for y in range(1985, 2001) for m in range(1, 13)}
    split = publication_split(rets, 1985, 1990, 1993)
    assert [x.label for x in split.spans] == ["in sample", "after the sample, before publication", "after publication"]
    assert [x.n for x in split.spans] == [72, 36, 84]
    assert split.spans[0].mean == pytest.approx(2.0) and split.spans[2].mean == pytest.approx(0.5)
    assert split.decay == pytest.approx(0.75)


def test_a_quintile_spread_converts_to_an_ic_through_the_normal_score_gap():
    """Grinold and Kahn (2000): a score with IC c spreads the top and bottom q tails' returns by
    c * sigma * 2 phi(z_q) / q. A 1%/month quintile spread with 8% cross-sectional spread is IC ~0.0446."""
    k = 2 * 0.2799619 / 0.2
    assert ic_from_spread(1.0, 0.2, 21, sigma=0.08) == pytest.approx(0.01 / (0.08 * k), rel=1e-5)
    assert ic_from_spread(1.0, 0.2, 5, sigma=0.08) == pytest.approx(0.01 / (0.08 * k) * math.sqrt(5 / 21), rel=1e-5)
    assert ic_from_spread(-0.5, 0.1, 21) < 0


def test_the_power_formula_matches_next_signals_recorded_formula_values():
    """next-signals spec and issue 02 Comments: ~480 names over the confirm window (~1680 days from
    2020) give the formula's 0.01 at 20 days and 0.005 at 5 days."""
    assert power(480, 1680, 20) == pytest.approx(0.010, abs=0.0005)
    assert power(480, 1680, 5) == pytest.approx(0.005, abs=0.0003)


@pytest.mark.parametrize("h, ic, t, recorded", [
    (20, 0.0079, 0.50, 0.032),   # momentum_12_1 confirm (next-signals issue 02 Comments)
    (5, 0.0036, 0.53, 0.0135),   # reversal_1m confirm (issue 02)
    (60, 0.0056, 0.37, 0.030),   # sue confirm (issue 04)
    (20, 0.0086, 0.87, 0.020),   # earnings_yield confirm (issue 04)
])
def test_the_detectable_ic_reproduces_next_signals_recorded_observed_power(h, ic, t, recorded):
    found = detectable_ic([(h, ic / t)], h, bar=2.0)
    assert found.value == pytest.approx(recorded, abs=0.0006) and found.same_horizon == 1


def test_the_detectable_ic_prefers_the_same_horizon_and_scales_others_by_root_h():
    obs = [(20, 0.0079 / 0.50), (20, 0.0086 / 0.87), (5, 0.0036 / 0.53)]
    at20 = detectable_ic(obs, 20, bar=3.0)
    assert at20.same_horizon == 2 and at20.value == pytest.approx(3.0 * (0.0158 + 0.009885) / 2, rel=1e-3)
    at10 = detectable_ic(obs, 10, bar=2.0)
    assert at10.same_horizon == 0 and at10.used == 3
    se = sorted([0.0158 * math.sqrt(10 / 20), 0.009885 * math.sqrt(10 / 20), 0.003600 / 0.53 * math.sqrt(10 / 5)])
    assert at10.value == pytest.approx(2.0 * se[1], rel=1e-3)
    assert detectable_ic([], 20, bar=2.0) is None
    assert detectable_ic([(20, 0.0)], 20, bar=2.0) is None


def test_a_strategy_sees_an_excess_of_z_tracking_errors_over_root_years():
    assert detectable_excess(0.05, 6.75) == pytest.approx(1.6449 * 0.05 / math.sqrt(6.75), rel=1e-4)
