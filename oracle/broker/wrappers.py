"""Where the next pound goes (boring-plan 09): workplace pension tax relief, employer money and salary
sacrifice, Lifetime ISA bonus and charge, and what a pension pays out after tax. Numbers in, numbers
out, at the dated rates in `uk_rates`. Marginal rates are read at the salary given: a contribution
bigger than the income in its band gets less than shown, and the £100,000 to £125,140 taper zone
more."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oracle.broker.projection import FeeModel
from oracle.broker.uk_rates import RATES, UkRates


def marginal_tax(salary: float, r: UkRates = RATES) -> float:
    if salary <= r.personal_allowance:
        return 0.0
    if salary <= r.basic_limit:
        return r.basic
    return r.higher if salary <= r.additional_from else r.additional


def marginal_ni(salary: float, r: UkRates = RATES) -> float:
    if salary <= r.ni_from:
        return 0.0
    return r.ni_main if salary <= r.ni_upper else r.ni_above


def qualifying_earnings(salary: float, r: UkRates = RATES) -> float:
    return max(0.0, min(salary, r.qualifying_upper) - r.qualifying_lower)


def higher_relief(gross: float, salary: float, r: UkRates = RATES) -> float:
    """The extra relief a higher or additional rate taxpayer claims on a relief-at-source `gross`
    a year (Self Assessment): the rate above basic on the part of `gross` matched by income taxed at
    that rate, top slice first."""
    top = min(gross, max(0.0, salary - r.additional_from))
    mid = min(gross - top, max(0.0, min(salary, r.additional_from) - r.basic_limit))
    return top * (r.additional - r.basic) + mid * (r.higher - r.basic)


@dataclass(frozen=True)
class Workplace:
    salary: float
    """£ a year before tax."""
    employee: float
    """Your contribution as a share of the earnings it is worked out on, relief included (0.05 is 5%)."""
    employer: float
    on_salary: bool = False
    """True: worked out on the whole salary. False: on qualifying earnings (the auto-enrolment band)."""
    sacrifice: bool = False
    """Paid by salary sacrifice: no relief at source; tax and NI saved on the salary given up."""

    @property
    def basis(self) -> float:
        return self.salary if self.on_salary else qualifying_earnings(self.salary)


@dataclass(frozen=True)
class PensionMonth:
    gross: float
    """Your contribution into the pension, relief included."""
    employer: float
    paid: float
    """Taken from your pay: the net amount under relief at source, all of `gross` under sacrifice."""
    relief: float
    """Added by the scheme at source (basic rate)."""
    refund: float
    """Higher-rate relief you claim back yourself: paid to you, not into the pension."""
    saved: float
    """Tax and NI your sacrifice saves."""

    @property
    def cost(self) -> float:
        """Take-home pay given up."""
        return self.paid - self.refund - self.saved

    @property
    def into(self) -> float:
        return self.gross + self.employer


def ni_room(sacrificed_yearly: float, when: date, r: UkRates = RATES) -> float:
    """£ a year of further sacrifice still free of NI on `when`."""
    if when < r.sacrifice_cap_from:
        return float("inf")
    return max(0.0, r.sacrifice_ni_cap - sacrificed_yearly)


def workplace_month(w: Workplace, when: date, r: UkRates = RATES) -> PensionMonth:
    gross, employer = w.basis * w.employee / 12, w.basis * w.employer / 12
    if w.sacrifice:
        free = min(gross * 12, ni_room(0.0, when, r)) / 12
        saved = gross * marginal_tax(w.salary, r) + free * marginal_ni(w.salary, r)
        return PensionMonth(gross, employer, gross, 0.0, 0.0, saved)
    net = gross * (1 - r.basic)
    return PensionMonth(gross, employer, net, gross - net, higher_relief(gross * 12, w.salary, r) / 12, 0.0)


def gross_for(cost: float, salary: float, sacrifice: bool, ni_room: float = float("inf"),
              r: UkRates = RATES) -> float:
    """What a pension gets for `cost` of take-home pay at the margin. Relief at source adds the
    basic rate even to non-taxpayers and the rest comes back by claim (put back in); sacrifice saves
    tax and NI, NI only on the first `ni_room` (same period as `cost`)."""
    tax = marginal_tax(salary, r)
    if not sacrifice:
        return cost / (1 - max(r.basic, tax))
    ni = marginal_ni(salary, r)
    gross = cost / (1 - tax - ni)
    return gross if gross <= ni_room else (cost + ni * ni_room) / (1 - tax)


def lisa_bonus(own: float, paid_this_year: float, r: UkRates = RATES) -> float:
    """The government's bonus on `own` paid in, after `paid_this_year` already went in this tax year."""
    return max(0.0, min(own, r.lisa_limit - paid_this_year)) * r.lisa_bonus


def lisa_withdrawal(value: float, r: UkRates = RATES) -> float:
    """What a withdrawal before 60 (not for a first home) leaves after the charge."""
    return value * (1 - r.lisa_charge)


def nest_fees(yearly_pct: float | None = None, contribution_pct: float | None = None,
              r: UkRates = RATES) -> FeeModel:
    """A pension scheme's charges as a FeeModel; Nest's published ones unless given (in %)."""
    yearly = r.nest_yearly_charge if yearly_pct is None else yearly_pct / 100
    each = r.nest_contribution_charge if contribution_pct is None else contribution_pct / 100
    return FeeModel(percent=yearly, fx=each)


def pension_spendable(value: float, retire_tax: float, r: UkRates = RATES) -> float:
    """A pot taken out: the tax-free share (up to the lump sum allowance) and the rest taxed at
    `retire_tax`."""
    free = min(value * r.tax_free_share, r.lump_sum_allowance)
    return free + (value - free) * (1 - retire_tax)


def access_age(born: int, r: UkRates = RATES) -> int:
    """The earliest pension age for someone born in `born`: 57 for anyone 55 on or after the change
    (1973 is taken as after; some older scheme members keep 55)."""
    return r.access_age if born + 55 >= r.access_age_from.year else 55
