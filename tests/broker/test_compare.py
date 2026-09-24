from datetime import date

import pytest

from oracle.broker.compare import benchmark_return, buy_and_hold, max_fall, returns_since

D1, D2, D3, D4 = (date(2026, 9, d) for d in (21, 22, 23, 24))


def approx(points):
    return [(d, pytest.approx(v)) for d, v in points]


def test_returns_start_at_zero_on_the_start_day_and_drop_earlier_days():
    series = [(date(2026, 9, 18), 9_000.0), (D1, 10_000.0), (D2, 11_000.0), (D3, 9_500.0)]
    assert returns_since(series, D1) == approx([(D1, 0.0), (D2, 0.1), (D3, -0.05)])


def test_returns_start_from_the_first_record_on_or_after_the_start_day():
    assert returns_since([(D2, 200.0), (D3, 250.0)], D1) == approx([(D2, 0.0), (D3, 0.25)])
    assert returns_since([], D1) == [] and returns_since([(D1, 5.0)], D2) == []


def test_max_fall_is_the_largest_peak_to_trough_fall():
    # Peak 120 to trough 90 is -25%; the later 110 to 100 is only -9%.
    assert max_fall([(D1, 100.0), (D2, 120.0), (D3, 90.0), (D4, 110.0)]) == pytest.approx(-0.25)
    assert max_fall([(D1, 100.0), (D2, 110.0)]) == 0.0
    assert max_fall([]) == 0.0


def test_buy_and_hold_splits_the_capital_equally_at_the_first_close_by_hand():
    # 500 each: A buys 5 at 100, B buys 25 at 20. Day 2: 5*110 + 25*16 = 950.
    series, left_out = buy_and_hold({"A": [(D1, 100.0), (D2, 110.0)], "B": [(D1, 20.0), (D2, 16.0)]}, D1, 1000.0)
    assert series == approx([(D1, 1000.0), (D2, 950.0)]) and left_out == []


def test_buy_and_hold_ignores_closes_before_the_start_day():
    series, _ = buy_and_hold({"A": [(date(2026, 9, 18), 50.0), (D1, 100.0), (D2, 120.0)]}, D1, 1000.0)
    assert series == approx([(D1, 1000.0), (D2, 1200.0)])


def test_a_missing_day_carries_the_last_value_forward():
    # B has no close on day 2 (a holiday), nor a converted one on day 3 (no rate): its 25 shares stay at 20.
    closes = {"A": [(D1, 100.0), (D2, 110.0), (D3, 120.0)], "B": [(D1, 20.0), (D3, None), (D4, 24.0)]}
    series, _ = buy_and_hold(closes, D1, 1000.0)
    assert series == approx([(D1, 1000.0), (D2, 1050.0), (D3, 1100.0), (D4, 1200.0)])


def test_a_listing_first_priced_after_the_start_is_held_as_cash_until_then():
    # B's 500 waits as cash until its first close on day 2, then buys 10 at 50.
    series, _ = buy_and_hold({"A": [(D1, 100.0), (D2, 100.0), (D3, 100.0)], "B": [(D2, 50.0), (D3, 60.0)]},
                             D1, 1000.0)
    assert series == approx([(D1, 1000.0), (D2, 1000.0), (D3, 1100.0)])


def test_a_listing_with_no_closes_in_range_is_left_out_and_named():
    closes = {"A": [(D1, 100.0), (D2, 110.0)], "B": [], "C": [(date(2026, 9, 18), 5.0), (D2, None)]}
    series, left_out = buy_and_hold(closes, D1, 1000.0)
    assert series == approx([(D1, 1000.0), (D2, 1100.0)]) and left_out == ["B", "C"]


def test_nothing_to_hold_is_an_empty_series():
    assert buy_and_hold({}, D1, 1000.0) == ([], [])
    assert buy_and_hold({"A": []}, D1, 1000.0) == ([], ["A"])


def test_benchmark_return_is_the_close_series_since_the_start_day():
    closes = [(D1, 100.0), (D2, 110.0), (D3, 121.0)]
    assert benchmark_return(closes, D1) == approx([(D1, 0.0), (D2, 0.1), (D3, 0.21)])


def test_benchmark_return_skips_a_missing_or_unconverted_close():
    closes = [(D1, 100.0), (D2, None), (D3, 150.0)]
    assert benchmark_return(closes, D1) == approx([(D1, 0.0), (D3, 0.5)])


def test_benchmark_return_is_empty_with_no_close_from_the_start_day_on():
    assert benchmark_return([(date(2026, 9, 18), 100.0)], D1) == []
    assert benchmark_return([], D1) == []
