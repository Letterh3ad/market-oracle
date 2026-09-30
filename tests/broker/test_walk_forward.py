import math
from datetime import date, timedelta

import pytest

from oracle.broker.walk_forward import Figures, figures, judge, segments

D = date(2024, 1, 1)


def series(values, start=D):
    return [(start + timedelta(days=k), v) for k, v in enumerate(values)]


def test_segments_run_back_to_back_six_months_each_from_the_first_day():
    got = segments(date(2022, 9, 21), date(2024, 3, 25), 6)
    assert got == [(date(2022, 9, 21), date(2023, 3, 20)), (date(2023, 3, 21), date(2023, 9, 20)),
                   (date(2023, 9, 21), date(2024, 3, 20))]


def test_a_tail_of_half_a_segment_or_more_is_its_own_shorter_segment():
    assert segments(date(2024, 1, 31), date(2024, 10, 30), 6) == [
        (date(2024, 1, 31), date(2024, 7, 30)), (date(2024, 7, 31), date(2024, 10, 30))]
    assert segments(date(2024, 1, 31), date(2024, 10, 29), 6) == [(date(2024, 1, 31), date(2024, 7, 30))]


def test_too_short_a_span_has_no_segment():
    assert segments(date(2024, 1, 1), date(2024, 3, 30), 6) == []


def test_figures_from_daily_equity():
    f = figures(series([100, 110, 99, 121]))
    daily = [0.1, -0.1, 121 / 99 - 1]
    mean = sum(daily) / 3
    sd = math.sqrt(sum((r - mean) ** 2 for r in daily) / 2)
    assert f.ret == pytest.approx(0.21) and f.max_fall == pytest.approx(-0.1)
    assert f.sharpe == pytest.approx(mean / sd * math.sqrt(252))
    assert f.per_fall == pytest.approx(2.1) and f.excess is None


def test_no_fall_or_no_spread_has_no_ratio():
    f = figures(series([100, 100, 100]))
    assert (f.ret, f.max_fall, f.sharpe, f.per_fall) == (0.0, 0.0, None, None)
    assert figures(series([100])) == Figures(0.0, 0.0, None, None)
    assert figures([]) == Figures(0.0, 0.0, None, None)


def test_each_line_is_judged_per_segment_against_the_benchmark_and_over_all_of_them():
    bh = [series([100, 110]), series([100, 95, 90], D + timedelta(days=200))]
    up = [series([100, 120, 115]), series([100, 101, 99], D + timedelta(days=200))]
    lines = judge({"buy_and_hold": bh, "x": up}, "buy_and_hold")
    held, x = lines
    assert (held.name, held.beat) == ("buy_and_hold", None)
    assert x.beat == 2 and [s.excess for s in x.segments] == pytest.approx([0.15 - 0.1, -0.01 + 0.1])
    assert x.overall.ret == pytest.approx(1.15 * 0.99 - 1)
    assert x.overall.excess == pytest.approx((1.15 * 0.99 - 1) - (1.1 * 0.9 - 1))
    assert x.overall.max_fall == pytest.approx(min(115 / 120 - 1, 99 / 101 - 1))
    pooled = [0.2, 115 / 120 - 1, 0.01, 99 / 101 - 1]
    mean = sum(pooled) / 4
    sd = math.sqrt(sum((r - mean) ** 2 for r in pooled) / 3)
    assert x.overall.sharpe == pytest.approx(mean / sd * math.sqrt(252))
    assert held.segments[0].excess == 0.0 and held.overall.excess == 0.0


def test_a_tie_with_the_benchmark_is_not_a_win():
    lines = judge({"buy_and_hold": [series([100, 110])], "x": [series([50, 55])]}, "buy_and_hold")
    assert lines[1].beat == 0
