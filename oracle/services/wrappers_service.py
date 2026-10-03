"""Where the next pound goes (boring-plan 09): the workplace pension (relief, employer money, salary
sacrifice), the Stocks and Shares ISA, and the Lifetime ISA when turned on, each projected with the
one contribution projection (`fees_service.project`, its `extra` hook carrying relief, bonus and
employer money), then the suggested order. Your numbers come from [wrappers] and [plan] monthly;
what is not entered is listed as missing, never assumed. Rates are the dated block in `uk_rates`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from oracle.broker.projection import steady
from oracle.broker.uk_rates import RATES, SOURCES
from oracle.broker.wrappers import (
    PensionMonth, Workplace, access_age, gross_for, lisa_bonus, lisa_withdrawal, nest_fees, ni_room,
    pension_spendable, workplace_month,
)
from oracle.config import WrappersConfig
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.fees_service import ASSUMED_GROWTH, FeeModel, Month, project, user_fees
from oracle.services.settings_service import set_wrappers

__all__ = ["PER", "Missing", "Step", "WrapperComparison", "WrapperRow", "check_growth", "save_wrappers", "wrapper_comparison"]

PER = 100.0
"""The rows compare this much take-home pay a month."""
LABELS = {"isa": "Stocks and Shares ISA", "pension": "Workplace pension", "lisa": "Lifetime ISA"}


@dataclass(frozen=True)
class WrapperRow:
    key: str
    label: str
    paid_in: float
    """Take-home pay given up, all months."""
    added: float
    """Relief, bonus or tax and NI saved, all months."""
    fees: float
    pot: float
    """At the pension age."""
    spendable: float
    """What the pot gives you if taken at the pension age: after tax, or after the Lifetime ISA charge."""
    at_60: float | None
    """Spendable at 60 with nothing more paid in (shown with the Lifetime ISA, whose charge ends then)."""
    access: str
    per_pound: float
    """Spendable per £1 of take-home paid in, at 60 when shown: the order is read from it (the Lifetime
    ISA stops taking money at 50, so the totals are not comparable)."""


@dataclass(frozen=True)
class Step:
    wrapper: str
    monthly: float
    """Take-home pay a month."""
    into: float
    """What that puts into the wrapper a month."""
    text: str


@dataclass(frozen=True)
class Missing:
    step: str
    """salary, born, needed or monthly."""
    text: str


@dataclass(frozen=True)
class WrapperComparison:
    settings: WrappersConfig
    lisa: bool
    monthly: float
    growth: float
    age: int | None
    access_age: int | None
    years: int | None
    """Years to the pension age."""
    paid_years: int | None
    """Years each row pays in (to 50 when the Lifetime ISA is shown)."""
    workplace: PensionMonth | None
    rows: tuple[WrapperRow, ...]
    steps: tuple[Step, ...]
    isa_yearly: float
    """£ a year the steps put into ISAs (the Lifetime ISA included)."""
    isa_allowance: float
    missing: tuple[Missing, ...]
    notes: tuple[str, ...]
    tax_year: str = RATES.tax_year
    checked: date = RATES.checked
    sources: tuple[tuple[str, str], ...] = field(default=SOURCES)


def save_wrappers(ctx: AppContext, values: dict[str, object]) -> WrappersConfig:
    """Any [wrappers] keys in one checked write (the Lifetime ISA switch among them)."""
    return set_wrappers(ctx, values)


def _gbp(x: float) -> str:
    return f"£{x:,.0f}" if abs(x) >= 100 else f"£{x:,.2f}"


def _missing(w: WrappersConfig, monthly: float, access: str) -> list[Missing]:
    out = []
    if not w.salary:
        out.append(Missing("salary", "Enter your salary and workplace pension percentages (Where the next pound goes)."))
    if not w.born:
        out.append(Missing("born", "Enter your year of birth: it sets when the pension and Lifetime ISA can be drawn."))
    if w.needed_before < 0:
        out.append(Missing("needed", f"Say how much of your monthly amount you may need before {access} "
                                     "(0 if none): that part belongs in the ISA."))
    if not monthly:
        out.append(Missing("monthly", "Set your monthly amount (Your plan card)."))
    return out


class _Rows:
    """The per-£100 projections, to the pension age and, with the Lifetime ISA, on to 60."""

    def __init__(self, ctx: AppContext, w: WrappersConfig, age: int, access: int, growth: float, sacrificed: float):
        self.w, self.age, self.access, self.sacrificed = w, age, access, sacrificed
        self.lisa_open = w.lisa and RATES.lisa_open_from <= age < RATES.lisa_open_before
        self.to_access = (access - age) * 12
        self.to_end = max(self.to_access, (RATES.lisa_free_age - age) * 12) if self.lisa_open else self.to_access
        self.paying = min(self.to_access, (RATES.lisa_pay_until - age) * 12) if self.lisa_open else self.to_access
        """Months paid in: with the Lifetime ISA, every row pays only while it can (to 50), so each £1 has
        the same time to grow."""
        self.series = steady(growth, self.to_end, ctx.clock.utcnow().date())
        account = user_fees(ctx)
        self.platform = FeeModel(percent=account.percent, cap=account.cap, fx=account.fx)
        """The flat fee and the monthly buy's dealing fee are paid anyway, so they are left out."""
        self.account = ctx.config.plan.monthly
        """The ISA account's own monthly stream: a capped percentage fee is per account, so the ISA row is
        charged the account's fees with the extra £100 less its fees without."""
        self.pension_fees = nest_fees(w.pension_fee, w.pension_charge)

    def pension_gross(self, own: float, when: date) -> float:
        """What `own` of take-home a month puts into the pension on `when`."""
        return gross_for(own, self.w.salary, self.w.sacrifice, ni_room(self.sacrificed, when) / 12)

    def pension_extra(self, own: float, i: int) -> float:
        return self.pension_gross(own, self.series[i][0]) - own

    def build(self) -> tuple[WrapperRow, ...]:
        paid = [PER] * self.paying
        rows = [self._row("isa", self.platform, paid, None, "any time"),
                self._row("pension", self.pension_fees, paid,
                          lambda i, own: self.pension_extra(own, i), f"from {self.access}")]
        if self.lisa_open:
            year = [0.0]

            def bonus(i: int, own: float) -> float:
                if i % 12 == 0:
                    year[0] = 0.0
                b = lisa_bonus(own, year[0])
                year[0] += own
                return b

            rows.append(self._row("lisa", self.platform, paid, bonus,
                                  f"from {RATES.lisa_free_age} (or a first home); {RATES.lisa_charge:.0%} charge before"))
        return tuple(rows)

    def _marginal(self, paid: list[float]) -> list[Month]:
        """The ISA account with the extra money less the account without it, month by month."""
        base = project(self.series, [self.account] * len(paid), self.platform)
        more = project(self.series, [self.account + p for p in paid], self.platform)
        return [Month(b.day, m.contributed - b.contributed, 0.0, m.fees - b.fees, m.value - b.value)
                for b, m in zip(base, more)]

    def _row(self, key: str, fees: FeeModel, paid: list[float], extra, access: str) -> WrapperRow:
        path = self._marginal(paid) if key == "isa" else project(self.series, paid, fees, extra)
        end = path[self.to_access - 1]
        spendable = self._spend(key, end, early=self.access < RATES.lisa_free_age)
        at_60 = self._spend(key, path[-1], early=False) if self.lisa_open else None
        judged = spendable if at_60 is None else at_60
        return WrapperRow(key, LABELS[key], end.contributed, end.extra, end.fees, end.value, spendable, at_60, access,
                          judged / end.contributed if end.contributed else 0.0)

    def _spend(self, key: str, m: Month, early: bool) -> float:
        if key == "pension":
            return pension_spendable(m.value, self.w.retire_tax / 100)
        if key == "lisa" and early:
            return lisa_withdrawal(m.value)
        return m.value


def check_growth(growth: float) -> None:
    if not -0.5 < growth < 0.5:
        raise InvalidQuery("growth is a yearly share, e.g. 0.05 for 5%")


def wrapper_comparison(ctx: AppContext, growth: float = ASSUMED_GROWTH) -> WrapperComparison:
    check_growth(growth)
    w, monthly = ctx.config.wrappers, ctx.config.plan.monthly
    today = ctx.clock.utcnow().date()
    age = today.year - w.born if w.born else None
    access = access_age(w.born) if w.born else None
    missing = _missing(w, monthly, f"age {access}" if access else "the pension age")
    notes: list[str] = []
    workplace = table = None
    rows: tuple[WrapperRow, ...] = ()
    if w.salary:
        workplace = workplace_month(Workplace(w.salary, w.employee_pct / 100, w.employer_pct / 100, w.on_salary,
                                              w.sacrifice), today)
        notes += _workplace_notes(w, workplace)
    if w.lisa and age is not None and not RATES.lisa_open_from <= age < RATES.lisa_open_before:
        notes.append(f"A Lifetime ISA can only be opened from {RATES.lisa_open_from} and before "
                     f"{RATES.lisa_open_before}, so it is left out. Age here is this year less your year of birth, so "
                     f"you may be a year younger (open one before your birthday if so). One already open keeps "
                     f"taking money until {RATES.lisa_pay_until}; this comparison does not show it.")
    if w.born and 1971 <= w.born <= 1973:
        notes.append(f"Born between 6 April 1971 and 5 April 1973 without a protected pension age, you may reach 55 "
                     f"and then lose access from {RATES.access_age_from:%d %B %Y} until 57: check with your scheme.")
    years = access - age if access is not None and age is not None else None
    if years is not None and years <= 0:
        notes.append(f"You can draw a pension now (from {access}): this comparison, of years still to grow, is not shown.")
    elif w.salary and years is not None:
        sacrificed = workplace.gross * 12 if w.sacrifice else 0.0
        table = _Rows(ctx, w, age, access, growth, sacrificed)
        rows = table.build()
        notes += _row_notes(w, growth, table)
    steps, isa_yearly = _steps(w, workplace, monthly, access, table, rows, notes)
    return WrapperComparison(w, w.lisa, monthly, growth, age, access, years, table.paying // 12 if table else None,
                             workplace, rows, steps,
                             isa_yearly, RATES.isa_allowance, tuple(missing), tuple(notes))


def _workplace_notes(w: WrappersConfig, m: PensionMonth) -> list[str]:
    out = []
    if w.salary < RATES.enrol_trigger:
        out.append(f"Below {_gbp(RATES.enrol_trigger)} a year your employer does not have to enrol you; you can "
                   "still ask to join.")
    elif not w.on_salary and (w.employer_pct / 100 < RATES.employer_min
                              or (w.employee_pct + w.employer_pct) / 100 < RATES.total_min):
        out.append(f"These percentages are under the auto-enrolment minimum ({RATES.employer_min:.0%} employer, "
                   f"{RATES.total_min:.0%} together, on qualifying earnings): check your payslip.")
    if m.into * 12 > RATES.annual_allowance:
        out.append(f"{_gbp(m.into * 12)} a year is over the {_gbp(RATES.annual_allowance)} annual allowance: "
                   "the excess is taxed.")
    if w.sacrifice and m.gross * 12 > RATES.sacrifice_ni_cap:
        out.append(f"From {RATES.sacrifice_cap_from:%d %B %Y} only the first {_gbp(RATES.sacrifice_ni_cap)} a year "
                   f"sacrificed is free of NI; you sacrifice {_gbp(m.gross * 12)}.")
    return out


def _row_notes(w: WrappersConfig, growth: float, table: _Rows) -> list[str]:
    paying = (f" Paid in until {RATES.lisa_pay_until} (the last Lifetime ISA year) in every row, so each £1 has "
              f"the same time to grow." if table.lisa_open else "")
    return [f"Each row is {_gbp(PER)} a month of take-home pay at {growth:.1%} a year (assumed), after charges: "
            f"the ISA and Lifetime ISA at the marginal cost of your platform (the flat fee and the monthly dealing "
            f"fee are paid anyway; the ISA pays what its percentage fee rises by on your monthly amount plus the "
            f"£100, so a cap counts once per account; the Lifetime ISA, its own account, pays the percentage up to "
            f"the cap on its own pot, and FX), the pension at {w.pension_fee:g}% a "
            f"year and {w.pension_charge:g}% of each contribution.{paying}",
            f"Pension spendable: {RATES.tax_free_share:.0%} tax-free (up to {_gbp(RATES.lump_sum_allowance)}), the "
            f"rest taxed at {w.retire_tax:g}% when drawn (your retire_tax; set the rate you expect).",
            "Relief is read at your salary's top rate; a contribution bigger than the income in that band gets "
            "less, and the £100,000 to £125,140 taper zone more."]


def _steps(w: WrappersConfig, m: PensionMonth | None, monthly: float, access: int | None, table: _Rows | None,
           rows: tuple[WrapperRow, ...], notes: list[str]) -> tuple[tuple[Step, ...], float]:
    steps: list[Step] = []
    if m is not None:
        steps.append(_workplace_step(w, m))
    if table is None or w.needed_before < 0 or not monthly:
        return tuple(steps), 0.0
    isa = min(w.needed_before, monthly)
    if isa:
        steps.append(Step("isa", isa, isa, f"Stocks and Shares ISA: {_gbp(isa)} a month for money you may need "
                                            f"before {access}. Tax-free, out any time."))
    rest = monthly - isa
    order = sorted(rows, key=lambda r: -r.per_pound)
    when = f"at {RATES.lisa_free_age}" if table.lisa_open else f"at {access}"
    why = (f" Money not needed before {access} goes where each £1 paid in gives most {when}: "
           + ", ".join(f"{r.label} £{r.per_pound:.2f}" for r in order) + ".")
    isa_yearly = isa * 12
    for r in order:
        if rest <= 0:
            break
        k = r.key
        amount = min(rest, RATES.lisa_limit / 12) if k == "lisa" else rest
        if k == "pension":
            into, text = _pension_step(w, amount, table, m, notes)
        else:
            into = amount * (1 + RATES.lisa_bonus) if k == "lisa" else amount
            text = f"{LABELS[k]}: {_gbp(amount)} a month puts {_gbp(into)} in."
        steps.append(Step(k, amount, into, text + why))
        why = " The rest goes to the next best."
        if k != "pension":
            isa_yearly += amount * 12
        rest -= amount
    if isa_yearly > RATES.isa_allowance:
        notes.append(f"These steps put {_gbp(isa_yearly)} a year into ISAs, over the {_gbp(RATES.isa_allowance)} "
                     f"allowance by {_gbp(isa_yearly - RATES.isa_allowance)}: that part goes outside an ISA, where "
                     "gains and dividends are taxed.")
    return tuple(steps), isa_yearly


def _pension_step(w: WrappersConfig, amount: float, table: _Rows, m: PensionMonth | None,
                  notes: list[str]) -> tuple[float, str]:
    """Extra pension for `amount` of take-home a month. Under sacrifice, `into` is what it puts in once the
    2029 NI cap applies when the paying years reach it (today's figure beside it); under relief at source, what
    leaves pay and what comes back."""
    reaches_cap = table.series[table.paying - 1][0] >= RATES.sacrifice_cap_from
    capped = table.pension_gross(amount, RATES.sacrifice_cap_from if reaches_cap else table.series[0][0])
    head = f"{LABELS['pension']}: {_gbp(amount)} a month of take-home"
    if w.sacrifice:
        now = table.pension_gross(amount, table.series[0][0])
        text = f"{head} puts {_gbp(capped)} in"
        if now - capped > 0.005:
            text += f" ({_gbp(now)} until {RATES.sacrifice_cap_from:%d %B %Y}, when NI is saved on the first " \
                    f"{_gbp(RATES.sacrifice_ni_cap)} a year sacrificed only)"
        own = (m.gross if m else 0.0) * 12
        total = own + capped * 12
        if reaches_cap and own <= RATES.sacrifice_ni_cap < total:
            notes.append(f"With this step you would sacrifice {_gbp(total)} a year: from "
                         f"{RATES.sacrifice_cap_from:%d %B %Y} only the first {_gbp(RATES.sacrifice_ni_cap)} is free "
                         "of NI, so each extra £1 saves less.")
        return capped, text + "."
    paid = capped * (1 - RATES.basic)
    back = paid - amount
    if back > 0.005:
        return capped, (f"{head}: you pay {_gbp(paid)}, the scheme adds {_gbp(capped - paid)} relief "
                        f"({_gbp(capped)} in), and {_gbp(back)} comes back through Self Assessment.")
    return capped, f"{head} puts {_gbp(capped)} in (relief at source included)."


def _workplace_step(w: WrappersConfig, m: PensionMonth) -> Step:
    parts = [f"{_gbp(m.relief)} relief"] if m.relief else []
    parts += [f"{_gbp(m.employer)} employer"] if m.employer else []
    parts += [f"{_gbp(m.saved)} tax and NI saved"] if m.saved else []
    back = f" You claim {_gbp(m.refund)} a month more back through Self Assessment." if m.refund else ""
    if m.employer and not w.employee_pct:
        head = (f"Workplace pension first: your employer's {w.employer_pct:g}% is money you get nowhere else. You pay "
                "nothing in: check whether paying some would bring more from your employer.")
    elif m.employer:
        head = (f"Workplace pension first: your {w.employee_pct:g}% brings your employer's {w.employer_pct:g}%, money "
                "you get nowhere else.")
    else:
        head = "No employer money: the workplace pension has no head start over the steps below."
    detail = (f" {_gbp(m.cost)} of take-home a month puts {_gbp(m.into)} in ({', '.join(parts)})." if m.into else "")
    return Step("pension", m.cost, m.into, head + detail + back)
