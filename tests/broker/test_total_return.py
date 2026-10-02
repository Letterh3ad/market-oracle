from datetime import date

import pytest

from oracle.broker.total_return import reinvest_factors, total_return


def d(n: int) -> date:
    return date(2026, 1, n)


def test_without_dividends_the_series_is_the_closes():
    closes = [(d(1), 100.0), (d(2), 110.0)]
    assert total_return(closes, []) == closes


def test_a_dividend_is_reinvested_at_its_ex_date_close():
    closes = [(d(1), 100.0), (d(2), 98.0), (d(3), 99.0)]
    out = total_return(closes, [(d(2), 2.0)])
    # (98 + 2) / 100: the drop on the ex-date is the dividend paid, so no loss.
    assert out[1][1] / out[0][1] == pytest.approx(1.0)
    assert out[2][1] / out[1][1] == pytest.approx(99.0 / 98.0)


def test_an_ex_date_without_a_bar_lands_on_the_next_one():
    closes = [(d(1), 100.0), (d(5), 98.0)]
    assert total_return(closes, [(d(3), 2.0)])[1][1] == pytest.approx(100.0)


def test_dividends_outside_the_closes_are_left_out():
    closes = [(d(5), 100.0), (d(6), 100.0)]
    assert total_return(closes, [(d(1), 5.0), (d(9), 5.0)]) == closes


def test_a_dividend_on_the_first_close_is_before_any_holding():
    closes = [(d(1), 100.0), (d(2), 100.0)]
    assert total_return(closes, [(d(1), 5.0)]) == closes


def test_a_day_without_a_close_keeps_its_gap_and_the_dividend_waits_for_a_close():
    closes = [(d(1), 100.0), (d(2), None), (d(3), 98.0)]
    out = total_return(closes, [(d(2), 2.0)])
    assert out[1] == (d(2), None)
    assert out[2][1] == pytest.approx(100.0)


def test_factors_compound():
    closes = [(d(1), 100.0), (d(2), 100.0), (d(3), 100.0)]
    f = reinvest_factors(closes, [(d(2), 1.0), (d(3), 1.0)])
    assert f == {d(1): 1.0, d(2): pytest.approx(1.01), d(3): pytest.approx(1.01 ** 2)}
