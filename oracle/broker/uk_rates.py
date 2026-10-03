"""UK tax rates and allowances the plan uses, in one dated block. Each figure was read on its gov.uk
(or scheme) page on `CHECKED`; re-check them each April. England, Wales and Northern Ireland income
tax bands (Scotland's differ; relief at source still adds 20% there)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class UkRates:
    tax_year: str
    checked: date

    personal_allowance: float
    basic_limit: float
    """Income above it pays the higher rate."""
    taper_from: float
    """The personal allowance shrinks £1 per £2 above it, so this zone pays 60% at the margin."""
    additional_from: float
    basic: float
    higher: float
    additional: float

    ni_from: float
    ni_upper: float
    ni_main: float
    ni_above: float

    qualifying_lower: float
    qualifying_upper: float
    enrol_trigger: float
    """Earnings from which an employer must enrol you."""
    employer_min: float
    total_min: float
    """Auto-enrolment minimum on qualifying earnings, employee and employer together."""

    access_age: int
    access_age_from: date
    """Normal minimum pension age is 55 until the day before this, then 57 (some older members keep 55)."""
    tax_free_share: float
    lump_sum_allowance: float
    annual_allowance: float
    sacrifice_ni_cap: float
    sacrifice_cap_from: date
    """From this day only the first `sacrifice_ni_cap` a year sacrificed into a pension is free of NI."""

    isa_allowance: float
    lisa_limit: float
    lisa_bonus: float
    lisa_charge: float
    lisa_open_from: int
    lisa_open_before: int
    lisa_pay_until: int
    lisa_free_age: int

    nest_contribution_charge: float
    nest_yearly_charge: float


RATES = UkRates(
    tax_year="2026-27", checked=date(2026, 10, 3),
    personal_allowance=12_570, basic_limit=50_270, taper_from=100_000, additional_from=125_140,
    basic=0.20, higher=0.40, additional=0.45,
    ni_from=12_570, ni_upper=50_270, ni_main=0.08, ni_above=0.02,
    qualifying_lower=6_240, qualifying_upper=50_270, enrol_trigger=10_000, employer_min=0.03, total_min=0.08,
    access_age=57, access_age_from=date(2028, 4, 6), tax_free_share=0.25, lump_sum_allowance=268_275,
    annual_allowance=60_000, sacrifice_ni_cap=2_000, sacrifice_cap_from=date(2029, 4, 6),
    isa_allowance=20_000, lisa_limit=4_000, lisa_bonus=0.25, lisa_charge=0.25, lisa_open_from=18,
    lisa_open_before=40,
    lisa_pay_until=50, lisa_free_age=60,
    nest_contribution_charge=0.018, nest_yearly_charge=0.003,
)

SOURCES = (
    ("Income tax bands and personal allowance taper", "https://www.gov.uk/income-tax-rates"),
    ("Employee National Insurance", "https://www.gov.uk/national-insurance/how-much-you-pay"),
    ("Auto-enrolment minimums and qualifying earnings",
     "https://www.gov.uk/workplace-pensions/what-you-your-employer-and-the-government-pay"),
    ("Earnings trigger and qualifying band 2026-27",
     "https://www.gov.uk/government/publications/review-of-the-automatic-enrolment-earnings-trigger-and-"
     "qualifying-earnings-band-for-202627"),
    ("Pension tax relief, relief at source, higher-rate claims",
     "https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief"),
    ("Minimum pension age 57 from 6 April 2028",
     "https://www.gov.uk/government/publications/increasing-normal-minimum-pension-age"),
    ("25% tax-free, lump sum allowance", "https://www.gov.uk/tax-on-your-private-pension/lump-sum-allowance"),
    ("Pension annual allowance", "https://www.gov.uk/tax-on-your-private-pension/annual-allowance"),
    ("Salary sacrifice NI cap from April 2029",
     "https://www.gov.uk/government/publications/changes-to-salary-sacrifice-for-pensions-from-april-2029"),
    ("ISA allowance", "https://www.gov.uk/individual-savings-accounts"),
    ("Lifetime ISA limits and bonus", "https://www.gov.uk/lifetime-isa"),
    ("Lifetime ISA withdrawal charge", "https://www.gov.uk/lifetime-isa/withdrawing-money-from-your-lifetime-isa"),
    ("Nest charges (scheme page, not gov.uk)",
     "https://nestpensions.org.uk/schemeweb/nest/advisers/using-nest/fees-and-charges.html"),
)

ISA_ALLOWANCE = RATES.isa_allowance
