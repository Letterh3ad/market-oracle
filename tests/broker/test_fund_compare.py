"""Fund comparison math (boring-plan 02): a fund's yearly charge taken day by day, a yearly
rebalanced mix, and the tracking difference between a real fund and its modelled proxy."""

from datetime import date, timedelta

import pytest

from oracle.broker.fund_compare import after_fees, blend, month_ends, on_days, tracking


def days(a: date, b: date) -> list[date]:
    return [a + timedelta(n) for n in range((b - a).days + 1)]


def test_a_flat_series_loses_exactly_the_ter_compounded_over_calendar_days():
    flat = [(d, 100.0) for d in days(date(2020, 1, 1), date(2022, 1, 1))]
    net = after_fees(flat, 0.002)
    assert net[0] == (date(2020, 1, 1), 100.0)
    assert net[-1][1] == pytest.approx(100 * 0.998 ** (731 / 365.25))
    assert net[-1][1] / 100 - 1 == pytest.approx(-0.004, abs=2e-5), "about 0.2% a year for two years"
    mid = dict(net)[date(2021, 1, 1)]
    assert mid == pytest.approx(100 * 0.998 ** (366 / 365.25))


def test_a_proxys_own_fee_is_added_back_before_the_funds_is_taken():
    flat = [(d, 1.0) for d in days(date(2020, 1, 1), date(2021, 1, 1))]
    assert after_fees(flat, 0.0032, embedded=0.0032)[-1][1] == pytest.approx(1.0)
    cheaper = after_fees(flat, 0.0019, embedded=0.0032)[-1][1]
    assert cheaper == pytest.approx((0.9981 / 0.9968) ** (366 / 365.25))
    assert cheaper > 1.0, "the UCITS fund charges less than the proxy did"


def test_a_mix_rebalances_to_its_weights_at_each_years_first_shared_day():
    a = [(date(2020, 1, 2), 1.0), (date(2020, 12, 31), 2.0), (date(2021, 1, 4), 2.0), (date(2021, 12, 31), 1.0)]
    b = [(date(2020, 1, 2), 1.0), (date(2020, 12, 31), 1.0), (date(2021, 1, 4), 1.0), (date(2021, 12, 31), 1.0),
         (date(2022, 1, 3), 1.0)]
    mixed = blend([(a, 0.5), (b, 0.5)])
    assert [d for d, _ in mixed] == [d for d, _ in a], "only the days every part has"
    assert dict(mixed)[date(2020, 12, 31)] == pytest.approx(1.5)
    assert mixed[-1][1] == pytest.approx(1.125), "rebalanced: 0.75 in each, then a halves"


def test_a_mix_rejects_weights_that_do_not_add_to_one():
    with pytest.raises(ValueError):
        blend([([(date(2020, 1, 2), 1.0)], 0.6)])


def test_on_days_takes_the_last_value_on_or_before_each_day():
    s = [(date(2020, 1, 2), 1.0), (date(2020, 1, 6), 2.0)]
    assert on_days(s, [date(2020, 1, 1), date(2020, 1, 3), date(2020, 1, 6)]) == [
        (date(2020, 1, 3), 1.0), (date(2020, 1, 6), 2.0)]


def test_month_ends_keep_each_months_last_point_and_the_first_and_last():
    s = [(date(2020, 1, 15), 1.0), (date(2020, 1, 31), 1.1), (date(2020, 2, 3), 1.2), (date(2020, 2, 10), 1.3)]
    assert month_ends(s) == [(date(2020, 1, 15), 1.0), (date(2020, 1, 31), 1.1), (date(2020, 2, 10), 1.3)]


def test_tracking_compares_from_the_real_funds_first_day_on_the_days_both_have():
    model = [(d, 1.0001 ** i) for i, d in enumerate(days(date(2019, 1, 1), date(2021, 1, 1)))]
    real = after_fees([(d, v) for d, v in model if d >= date(2019, 6, 1)], 0.001)
    t = tracking(real, model)
    assert (t.start, t.end) == (date(2019, 6, 1), date(2021, 1, 1))
    assert t.model_return == pytest.approx(model[-1][1] / dict(model)[date(2019, 6, 1)] - 1)
    assert t.difference == pytest.approx(t.real_return - t.model_return)
    assert t.yearly_difference == pytest.approx(-0.001, abs=1e-4), "the extra 0.1% a year shows as tracking"


def test_tracking_with_no_overlap_is_none():
    assert tracking([(date(2020, 1, 1), 1.0)], [(date(2021, 1, 1), 1.0), (date(2021, 2, 1), 1.0)]) is None
