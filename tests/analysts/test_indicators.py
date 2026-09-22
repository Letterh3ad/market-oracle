import math

import pytest

from oracle.analysts.indicators import macd, rsi, volatility

# StockCharts' worked example of Wilder's RSI (ChartSchool, "Relative Strength Index",
# the cs-rsi worksheet), closes and published RSI values.
STOCKCHARTS = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
    46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521,
    45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314,
]
PUBLISHED_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99,
                 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def test_rsi_matches_the_published_worked_example():
    values = rsi(STOCKCHARTS)
    assert values[:14] == [None] * 14
    assert [round(v, 2) for v in values[14:]] == PUBLISHED_RSI


def test_macd_on_a_straight_line_settles_at_seven_times_the_slope():
    # An n-day EMA of a line sits (n - 1) / 2 days behind it, so EMA12 - EMA26 = 7 slopes,
    # the signal line equals it, and the histogram is zero.
    line, signal, hist = macd([100 + 0.5 * t for t in range(60)])
    assert line[24] is None and line[25] == pytest.approx(3.5)
    assert signal[32] is None and signal[33] == pytest.approx(3.5)
    assert hist[-1] == pytest.approx(0.0)


def test_volatility_of_alternating_one_percent_moves():
    closes = [100.0]
    for i in range(20):
        closes.append(closes[-1] * 1.01 if i % 2 == 0 else closes[-1] / 1.01)
    expected = math.log(1.01) * math.sqrt(20 / 19) * math.sqrt(252)
    assert volatility(closes)[-1] == pytest.approx(expected)
    assert volatility(closes)[-2] is None
