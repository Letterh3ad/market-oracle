"""The predictive test's numbers: rank IC, Newey-West t, trailing fits and the fixed verdict."""

import math
from datetime import date, timedelta

import pytest

from oracle.analysts.predictive import (
    MIN_NAMES, SegmentIC, combine, date_only, fit_weights, known_moment, lookback, newey_west_t, own_return, pct_ranks,
    session_open, span_return, spearman, usable_day, verdict,
)
from oracle.market.bars import Bar
from tests.analysts.bars import weekdays


def test_spearman_is_one_for_the_same_order_and_minus_one_reversed():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert spearman(xs, [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert spearman(xs, [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_averages_tied_ranks():
    # Ranks x: 1.5 1.5 3 4 5, y: 1 2 3 4 5; Pearson of those.
    got = spearman([0, 0, 1, 2, 3], [1, 2, 3, 4, 5])
    assert got == pytest.approx(0.9746794, abs=1e-6)


def test_spearman_needs_enough_names_and_a_spread_on_both_sides():
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) is None, f"fewer than {MIN_NAMES}"
    assert spearman([0, 0, 0, 0, 0], [1, 2, 3, 4, 5]) is None, "every score equal"
    assert spearman([1, 2, 3, 4, 5], [1, 1, 1, 1, 1]) is None


def test_pct_ranks_average_ties_between_zero_and_one():
    assert pct_ranks([3, 1, 2]) == [1.0, 0.0, 0.5]
    assert pct_ranks([1, 1, 2]) == [0.25, 0.25, 1.0]
    assert pct_ranks([4]) == [0.5]


def test_newey_west_with_no_lags_is_the_plain_t():
    xs = [0.1, 0.2, -0.05, 0.15, 0.05, 0.12]
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    assert newey_west_t(xs, 0) == pytest.approx(mean / math.sqrt(var / n))


def test_newey_west_shrinks_t_when_neighbours_move_together():
    trend = [0.1 + 0.05 * math.sin(i / 3) for i in range(60)]
    assert newey_west_t(trend, 5) < newey_west_t(trend, 0)


def test_newey_west_needs_a_spread():
    assert newey_west_t([0.1], 1) is None
    assert newey_west_t([0.1, 0.1, 0.1], 1) is None


def test_weights_follow_each_features_trailing_ic_sign_included_and_sum_to_one_in_size():
    w = fit_weights({"a": [0.1] * 20, "b": [-0.3] * 25, "c": [0.2] * 5}, min_days=20)
    assert w == pytest.approx({"a": 0.25, "b": -0.75, "c": 0.0}), "c has too few days"
    assert fit_weights({"a": [0.0] * 30}, min_days=20) == {"a": 0.0}


def test_combined_score_is_the_weighted_percentile_ranks():
    features = {"a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0]}
    assert combine(features, {"a": 0.5, "b": -0.5}) == pytest.approx([-0.5, 0.0, 0.5])


def seg(ic, days=30, counted=True, why=None):
    return SegmentIC(date(2023, 1, 1), date(2023, 6, 30), ic, None, days, counted, why)


def test_the_bar_needs_ic_above_zero_t_above_two_and_a_majority_of_counted_segments():
    good = [seg(0.05), seg(0.02), seg(-0.01)]
    assert verdict(0.03, 2.5, good).passed
    assert not verdict(0.03, 1.9, good).passed, "t not above 2"
    assert not verdict(-0.01, 2.5, good).passed
    assert not verdict(0.03, 2.5, [seg(0.05), seg(-0.02), seg(-0.01)]).passed, "1 of 3 positive"
    split = verdict(0.03, 2.5, [seg(0.05), seg(-0.02)])
    assert not split.passed and split.positive == 1 and split.counted == 2, "half is not a majority"


def test_segments_left_out_do_not_vote_and_too_few_counted_is_inconclusive():
    v = verdict(0.03, 2.5, [seg(0.05), seg(0.04), seg(-0.2, counted=False, why="thin")])
    assert v.passed and (v.positive, v.counted) == (2, 2)
    few = verdict(0.03, 2.5, [seg(0.05), seg(-0.2, counted=False, why="thin")])
    assert not few.passed and few.label == "INCONCLUSIVE"
    assert verdict(None, None, []).label == "INCONCLUSIVE"


def bars(opens, closes=None, start=date(2025, 1, 6)):
    days = weekdays(len(opens), start)
    closes = closes or opens
    return [Bar("T", d, o, o, o, c, c, 1, "USD", "t") for d, o, c in zip(days, opens, closes)], days


def test_own_return_is_open_on_the_day_to_open_h_bars_later_with_its_exit_day():
    b, days = bars([100, 101, 102, 103, 110, 120])
    assert own_return(b, days, days[1], 3) == (pytest.approx(110 / 101 - 1), days[4])
    assert own_return(b, days, days[3], 3) is None, "no bar h later yet"
    assert own_return(b, days, date(2025, 1, 5), 1) is None, "no bar of its own that day"


def test_future_bars_after_the_exit_change_nothing():
    b, days = bars([100, 101, 102, 103, 110, 120, 130])
    cut = own_return(b[:5], days[:5], days[1], 3)
    assert own_return(b, days, days[1], 3) == cut


def test_a_split_sized_move_inside_the_window_drops_it():
    b, days = bars([100, 101, 40, 41, 42])
    assert own_return(b, days, days[0], 3) is None
    assert own_return(b, days, days[2], 2) is None, "the gap from the close before to the entry open"
    assert own_return(b, days, days[3], 1) is not None, "after the break is fine"


def test_a_peer_return_spans_its_first_bars_on_or_after_the_days_within_slack():
    b, days = bars([100, 102, 104, 106, 108])
    assert span_return(b, days, days[0], days[2]) == pytest.approx(0.04)
    assert span_return(b, days, days[0] - timedelta(days=2), days[2]) == pytest.approx(0.04), "weekend start"
    assert span_return(b, days, days[0], days[-1] + timedelta(days=3)) is None, "no exit bar"
    assert span_return(b, days, days[0] - timedelta(days=9), days[2]) is None, "start too far"


def test_lookback_sums_the_usable_days_up_to_the_decision_day_with_the_attention_baseline():
    d = date(2025, 3, 10)
    daily = {d: {"own": (2, 1.2, 0.8), "competitor": (4, -0.4, 1.0)},
             d - timedelta(days=6): {"own": (1, -0.2, 0.1)},
             d - timedelta(days=7): {"own": (5, 9.0, 5.0)},
             d + timedelta(days=1): {"own": (3, 7.0, 3.0)}}
    f = lookback(daily, d, 7, ("own", "competitor", "industry"))
    assert f == pytest.approx({"own_sum": 1.0, "own_mean": 1.0 / 3, "own_relevance": 0.9 / 3,
                               "own_attention": math.log(4), "competitor_sum": -0.4, "competitor_mean": -0.1,
                               "industry_sum": 0.0, "industry_mean": 0.0})


@pytest.mark.parametrize("stamp, only", [
    ("2023-07-03T07:00:00+00:00", True),   # midnight Pacific, summer
    ("2023-01-03T08:00:00+00:00", True),   # midnight Pacific, winter
    ("2023-03-01T00:00:00+00:00", True),   # an SEC filing day
    ("2023-01-03T07:00:00+00:00", False),  # 23:00 Pacific the day before
    ("2023-07-03T07:00:05+00:00", False),
    ("2023-07-03T13:45:00+00:00", False),
])
def test_a_date_only_stamp_is_midnight_pacific_or_utc(stamp, only):
    assert date_only(stamp) is only


def test_a_date_only_stamp_is_known_a_day_later_and_a_timed_one_when_stamped():
    assert known_moment("2023-07-03T07:00:00+00:00").isoformat() == "2023-07-04T07:00:00+00:00"
    assert known_moment("2023-07-03T13:45:00+00:00").isoformat() == "2023-07-03T13:45:00+00:00"


def test_each_listing_opens_at_its_own_exchanges_local_time_in_utc():
    assert session_open("AAPL", date(2023, 7, 3)).isoformat() == "2023-07-03T13:30:00+00:00"
    assert session_open("7203.T", date(2023, 7, 3)).isoformat() == "2023-07-03T00:00:00+00:00"
    assert session_open("BHP.AX", date(2023, 1, 3)).isoformat() == "2023-01-02T23:00:00+00:00", "Sydney summer"
    assert session_open("SHEL.L", date(2023, 7, 3)).isoformat() == "2023-07-03T07:00:00+00:00"
    assert session_open("X.ZZ", date(2023, 7, 3)).isoformat() == "2023-07-02T22:00:00+00:00", "unknown: earliest"


def test_a_google_stamp_reaches_tokyo_a_day_after_new_york():
    known = known_moment("2023-07-03T07:00:00+00:00")
    assert usable_day("7203.T", known) == date(2023, 7, 5), "known 16:00 JST on the 4th, after that open"
    assert usable_day("AAPL", known) == date(2023, 7, 4), "known 03:00 in New York on the 4th"
    assert usable_day("AAPL", known_moment("2023-07-04T13:30:00+00:00")) == date(2023, 7, 5), "at the open is late"
    assert usable_day("AAPL", known_moment("2023-07-04T13:29:00+00:00")) == date(2023, 7, 4)
