from datetime import date

import pytest

from oracle.broker.projection import (
    NO_FEES, FeeModel, Month, crossover, drip_schedule, month_ends, monthly_returns, project, steady,
)


def months(*closes: float) -> list[tuple[date, float]]:
    return [(date(2020 + (i // 12), i % 12 + 1, 28), c) for i, c in enumerate(closes)]


def test_crossover_is_the_pot_where_the_percentage_fee_equals_the_flat_fee():
    # By hand: £4.99 a month is £59.88 a year; 0.25% of the pot reaches that at 59.88 / 0.0025.
    assert crossover(FeeModel(percent=0.0025), FeeModel(flat=4.99)) == pytest.approx(23_952.0)


def test_a_cap_above_the_flat_fee_leaves_the_crossover_where_it_was():
    assert crossover(FeeModel(percent=0.0025, cap=100.0), FeeModel(flat=4.99)) == pytest.approx(23_952.0)


def test_a_cap_at_or_below_the_flat_fee_means_flat_never_wins():
    assert crossover(FeeModel(percent=0.0025, cap=45.0), FeeModel(flat=4.99)) is None


def test_no_percentage_fee_means_flat_never_wins():
    assert crossover(FeeModel(), FeeModel(flat=4.99)) is None


def test_a_free_flat_platform_wins_from_the_first_pound():
    assert crossover(FeeModel(percent=0.0025), FeeModel()) == 0.0


def test_platform_fee_is_a_twelfth_of_the_yearly_percentage_capped_a_twelfth_of_the_cap():
    assert FeeModel(percent=0.012).platform(10_000) == pytest.approx(10.0)
    assert FeeModel(percent=0.012, cap=60.0).platform(10_000) == pytest.approx(5.0)
    assert FeeModel(flat=4.99).platform(10_000) == pytest.approx(4.99)


def test_buying_costs_dealing_per_buy_and_fx_on_the_amount():
    assert FeeModel(dealing=1.5, fx=0.005).buying(200.0) == pytest.approx(2.5)
    assert FeeModel(dealing=1.5).buying(0.0) == 0.0


def test_flat_series_no_fees_value_is_what_went_in():
    path = project(months(100, 100, 100, 100), [100.0, 100.0, 100.0])
    assert [m.value for m in path] == pytest.approx([100.0, 200.0, 300.0])
    assert path[-1] == Month(date(2020, 4, 28), 300.0, 0.0, 0.0, pytest.approx(300.0))


def test_a_contribution_grows_with_the_series_from_the_month_it_arrives():
    path = project(months(100, 200, 200), [100.0, 100.0])
    assert [m.value for m in path] == pytest.approx([200.0, 300.0])


def test_platform_fee_comes_off_the_month_end_value():
    path = project(months(100, 100), [1000.0], FeeModel(percent=0.012))
    assert path[0].fees == pytest.approx(1.0)
    assert path[0].value == pytest.approx(999.0)


def test_buying_costs_come_off_before_investing():
    path = project(months(100, 200), [100.0], FeeModel(dealing=1.0, fx=0.005))
    assert path[0].fees == pytest.approx(1.5)
    assert path[0].value == pytest.approx(2 * 98.5)


def test_a_month_without_money_in_pays_no_dealing():
    path = project(months(100, 100, 100), [100.0], FeeModel(dealing=1.0))
    assert path[-1].fees == pytest.approx(1.0)


def test_extra_inflow_hook_adds_a_flat_inflow_every_month():
    path = project(months(100, 100, 100, 100), [100.0, 100.0, 100.0], extra=lambda i, paid: 25.0)
    assert path[-1].contributed == pytest.approx(300.0)
    assert path[-1].extra == pytest.approx(75.0)
    assert path[-1].value == pytest.approx(375.0)


def test_extra_inflow_hook_sees_the_month_and_the_contribution():
    seen = []
    project(months(100, 100, 100), [10.0, 20.0], extra=lambda i, paid: seen.append((i, paid)) or 0.0)
    assert seen == [(0, 10.0), (1, 20.0)]


def test_extra_inflow_grows_and_is_charged_like_the_contribution():
    path = project(months(100, 200), [80.0], FeeModel(fx=0.01), extra=lambda i, paid: paid * 0.25)
    assert path[0].value == pytest.approx(2 * 100.0 * 0.99)


def test_a_starting_pot_grows_too():
    assert project(months(100, 110), [0.0], start=1000.0)[0].value == pytest.approx(1100.0)


def test_contributions_beyond_the_series_are_refused():
    with pytest.raises(ValueError):
        project(months(100, 100), [1.0, 1.0])


def test_month_ends_keep_each_months_last_close_and_skip_gaps():
    daily = [(date(2024, 1, 30), 1.0), (date(2024, 1, 31), 2.0), (date(2024, 2, 1), None),
             (date(2024, 2, 28), 3.0), (date(2024, 2, 29), None)]
    assert month_ends(daily) == [(date(2024, 1, 31), 2.0), (date(2024, 2, 28), 3.0)]


def test_steady_series_compounds_a_yearly_rate_monthly():
    s = steady(0.05, 12, date(2026, 1, 31))
    assert len(s) == 13
    assert s[-1][1] / s[0][1] == pytest.approx(1.05)


def test_monthly_returns_are_close_to_close():
    assert monthly_returns(months(100, 110, 99)) == pytest.approx([0.1, -0.1])


def test_drip_schedule_spreads_the_cash_and_its_interest():
    assert drip_schedule(1200.0, 12) == pytest.approx([100.0] * 12)
    with_interest = drip_schedule(1200.0, 12, cash_rate=0.12)
    assert with_interest[0] == pytest.approx(100.0)
    assert sum(with_interest) > 1200.0


def test_no_fees_is_free():
    assert NO_FEES.platform(1e6) == 0.0 and NO_FEES.buying(1e6) == 0.0
