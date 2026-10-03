"""Worst falls inside a crash window, their recovery, and what paying in monthly through one does."""

from datetime import date, timedelta

import pytest

from oracle.broker.drawdowns import keep_paying, months, worst_fall


def daily(values: list[float], start: date = date(2020, 1, 1)) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_the_deepest_peak_to_trough_inside_the_window_and_the_first_day_back_at_the_peak():
    s = daily([100, 110, 90, 120, 60, 80, 119, 121, 130])
    f = worst_fall(s, date(2020, 1, 1), date(2020, 1, 6))
    assert (f.peak_day, f.peak, f.trough_day, f.trough) == (date(2020, 1, 4), 120, date(2020, 1, 5), 60)
    assert f.fall == pytest.approx(-0.5)
    assert f.recovered == date(2020, 1, 8)


def test_recovery_is_searched_past_the_window_and_is_none_when_never_reached():
    s = daily([100, 50, 70, 99])
    f = worst_fall(s, date(2020, 1, 1), date(2020, 1, 2))
    assert f.trough == 50 and f.recovered is None


def test_a_series_that_starts_after_the_window_opens_or_ends_before_it_closes_does_not_cover_it():
    s = daily([100] * 40 + [50] * 40)
    assert worst_fall(s, date(2019, 12, 1), date(2020, 2, 1)) is None
    assert worst_fall(s, date(2020, 1, 1), date(2020, 6, 1)) is None
    assert worst_fall(s, date(2019, 12, 26), date(2020, 2, 15)) is not None, "a few days' slack for holidays"


def test_a_window_with_no_fall_has_none():
    assert worst_fall(daily([1, 2, 3, 4]), date(2020, 1, 1), date(2020, 1, 4)) is None


def test_paying_in_monthly_buys_cheaper_units_so_money_in_is_back_before_the_price():
    days = [date(2020, 1, 31), date(2020, 2, 28), date(2020, 3, 31), date(2020, 4, 30), date(2020, 5, 29),
            date(2020, 6, 1)]
    s = list(zip(days, [100, 50, 50, 95, 100, 100]))
    f = worst_fall(s, days[0], days[3])
    m = keep_paying(s, f, pot=1000, monthly=100)
    # Peak 2020-01-31: 10 units. Feb end at 50: value 500, +2 units, paid 1100. Mar end at 50: 600, +2, 1200.
    assert m.paid_at_trough == pytest.approx(1100) and m.value_at_trough == pytest.approx(600)
    assert m.worst_gap == pytest.approx(600 - 1100) and m.worst_day == date(2020, 2, 28)
    # Apr end at 95: 14 units = 1330, 1430 after paying 100; paid 1300. Even in April, the price only in May.
    assert m.even == date(2020, 4, 30) and f.recovered == date(2020, 5, 29)
    assert m.paid_at_even == pytest.approx(1300)


def test_without_monthly_payments_even_is_the_price_recovery():
    s = daily([100, 60, 90, 100, 105])
    f = worst_fall(s, date(2020, 1, 1), date(2020, 1, 3))
    m = keep_paying(s, f, pot=5000, monthly=0)
    assert m.even == f.recovered and m.worst_gap == pytest.approx(-2000)


def test_months_between_two_days():
    assert months(date(2020, 1, 31), date(2020, 7, 31)) == 6
    assert months(date(2020, 1, 1), date(2020, 1, 20)) == 1, "part of a month counts as one"
    assert months(date(2020, 1, 1), date(2020, 1, 1)) == 0
