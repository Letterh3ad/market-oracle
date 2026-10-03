"""Pension, ISA and Lifetime ISA maths, checked against gov.uk's own worked examples (URLs in
`oracle.broker.uk_rates.SOURCES`)."""

from datetime import date

import pytest

from oracle.broker.projection import FeeModel, project, steady
from oracle.broker.uk_rates import RATES
from oracle.broker.wrappers import (
    Workplace, access_age, gross_for, higher_relief, lisa_bonus, lisa_withdrawal, marginal_ni, marginal_tax,
    nest_fees, pension_spendable, qualifying_earnings, workplace_month,
)

BEFORE_CAP = date(2027, 1, 31)
AFTER_CAP = date(2030, 1, 31)


def test_govuk_workplace_example_40_own_30_employer_10_relief_makes_80():
    # gov.uk/workplace-pensions/what-you-your-employer-and-the-government-pay: "you put in £40, your
    # employer puts in £30, you get £10 tax relief. A total of £80". 5% + 3% of £1,000 a month of
    # qualifying earnings (£18,240 salary less the £6,240 lower limit).
    m = workplace_month(Workplace(salary=18_240, employee=0.05, employer=0.03), BEFORE_CAP)
    assert (m.paid, m.employer, m.relief, m.into) == pytest.approx((40, 30, 10, 80))
    assert m.cost == pytest.approx(40) and m.refund == 0 and m.saved == 0


def test_govuk_higher_rate_example_extra_relief_on_the_10000_taxed_at_40():
    # gov.uk/tax-on-your-private-pension/pension-tax-relief: earn £60,270, pay £12,000 into a relief at
    # source scheme: "claim an extra 20% tax relief on £10,000".
    assert higher_relief(12_000, 60_270) == pytest.approx(2_000)
    assert higher_relief(5_000, 60_270) == pytest.approx(1_000)
    assert higher_relief(12_000, 40_000) == 0


def test_additional_rate_relief_takes_the_top_slice_first():
    # £5,000 of the £10,000 sits above £125,140 (25 points extra), the rest in the 40% band (20 extra).
    assert higher_relief(10_000, 130_140) == pytest.approx(5_000 * 0.25 + 5_000 * 0.20)


def test_qualifying_earnings_band():
    assert qualifying_earnings(5_000) == 0
    assert qualifying_earnings(30_000) == pytest.approx(30_000 - 6_240)
    assert qualifying_earnings(80_000) == pytest.approx(50_270 - 6_240)


@pytest.mark.parametrize("salary, tax, ni", [(10_000, 0, 0), (30_000, 0.20, 0.08), (60_000, 0.40, 0.02),
                                              (130_000, 0.45, 0.02)])
def test_marginal_rates(salary, tax, ni):
    assert marginal_tax(salary) == tax and marginal_ni(salary) == ni


def test_employer_on_whole_salary_and_higher_rate_refund():
    m = workplace_month(Workplace(salary=60_000, employee=0.05, employer=0.05, on_salary=True), BEFORE_CAP)
    assert m.gross == pytest.approx(250) and m.employer == pytest.approx(250)
    assert (m.paid, m.relief) == pytest.approx((200, 50))
    assert m.refund == pytest.approx(50)  # all £3,000 a year falls inside the £9,730 taxed at 40%
    assert m.cost == pytest.approx(150) and m.into == pytest.approx(500)


def test_salary_sacrifice_saves_tax_and_ni_and_the_2029_cap_limits_the_ni():
    w = Workplace(salary=30_000, employee=0.05, employer=0.03, sacrifice=True)
    m = workplace_month(w, BEFORE_CAP)
    gross = (30_000 - 6_240) * 0.05 / 12
    assert m.gross == pytest.approx(gross) and m.relief == 0 and m.paid == pytest.approx(gross)
    assert m.saved == pytest.approx(gross * 0.28) and m.cost == pytest.approx(gross * 0.72)
    # £1,188 a year is under the £2,000 cap: unchanged after April 2029.
    assert workplace_month(w, AFTER_CAP).saved == pytest.approx(m.saved)
    big = Workplace(salary=30_000, employee=0.10, employer=0.03, sacrifice=True, on_salary=True)
    after = workplace_month(big, AFTER_CAP)  # £3,000 a year sacrificed: NI saved only on £2,000
    assert after.saved == pytest.approx(250 * 0.20 + 2_000 / 12 * 0.08)


def test_gross_for_a_take_home_cost():
    assert gross_for(100, 30_000, sacrifice=False) == pytest.approx(125)
    assert gross_for(100, 8_000, sacrifice=False) == pytest.approx(125)  # relief at source for non-taxpayers too
    assert gross_for(100, 60_000, sacrifice=False) == pytest.approx(100 / 0.6)
    assert gross_for(100, 30_000, sacrifice=True) == pytest.approx(100 / 0.72)
    assert gross_for(100, 30_000, sacrifice=True, ni_room=0) == pytest.approx(125)
    g = gross_for(100, 30_000, sacrifice=True, ni_room=50)
    assert g == pytest.approx(130) and g * 0.8 - 50 * 0.08 == pytest.approx(100)
    assert gross_for(100, 8_000, sacrifice=True) == pytest.approx(100)  # no tax or NI to save


def test_govuk_lisa_example_800_gets_200_bonus_and_a_full_withdrawal_leaves_750():
    # gov.uk/lifetime-isa/withdrawing-money-from-your-lifetime-isa
    assert lisa_bonus(800, paid_this_year=0) == pytest.approx(200)
    assert lisa_withdrawal(1_000) == pytest.approx(750)


def test_lisa_bonus_stops_at_the_4000_limit():
    # gov.uk/lifetime-isa: "up to £4,000 each year", bonus "up to a maximum of £1,000 per year".
    assert lisa_bonus(500, paid_this_year=3_800) == pytest.approx(50)
    assert lisa_bonus(500, paid_this_year=4_000) == 0


def test_nest_charges_match_nests_example():
    # Nest: £1,000 paid in over a year costs £18; a £10,000 pot pays £30 a year.
    fees = nest_fees()
    assert fees.buying(1_000) == pytest.approx(18)
    path = project(steady(0.0, 12, date(2027, 1, 1)), [], fees, start=10_000)
    assert path[-1].fees == pytest.approx(30, abs=0.1)


def test_nest_fees_take_the_users_own_charges():
    assert nest_fees(0.5, 0.0) == FeeModel(percent=0.005, fx=0.0)


def test_pension_spendable_quarter_tax_free_rest_taxed_and_the_lump_sum_cap():
    assert pension_spendable(100_000, 0.20) == pytest.approx(25_000 + 75_000 * 0.8)
    big = 2_000_000
    assert pension_spendable(big, 0.20) == pytest.approx(268_275 + (big - 268_275) * 0.8)


@pytest.mark.parametrize("born, age", [(1990, 57), (1973, 57), (1972, 55)])
def test_access_age(born, age):
    assert access_age(born) == age


def test_rates_are_dated():
    assert RATES.tax_year == "2026-27" and RATES.isa_allowance == 20_000
