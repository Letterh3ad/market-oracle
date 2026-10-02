"""The contribution and fee projection (boring-plan spec 3-4): a monthly contribution schedule, a
platform's fees and a growth (total-return) series in, the pot's path out. Numbers in, numbers out;
the pension comparator (ticket 09) calls `project` with its own extra inflows (tax relief,
employer money)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

Series = Sequence[tuple[date, float]]
"""Month-end closes, oldest first: n + 1 closes make n months."""
Extra = Callable[[int, float], float]
"""(month index, the month's own contribution) -> money added beside it that month."""


@dataclass(frozen=True)
class FeeModel:
    percent: float = 0.0
    """Yearly platform fee as a share of the pot (0.0025 is 0.25%), charged monthly."""
    cap: float | None = None
    """£ a year the percentage fee stops at."""
    flat: float = 0.0
    """£ a month."""
    dealing: float = 0.0
    """£ per buy or sell."""
    fx: float = 0.0
    """Share of each buy taken for currency conversion."""

    def platform(self, pot: float) -> float:
        """One month's platform fee on a pot of `pot`."""
        fee = pot * self.percent / 12
        if self.cap is not None:
            fee = min(fee, self.cap / 12)
        return fee + self.flat

    def buying(self, amount: float, trades: int = 1) -> float:
        return 0.0 if amount <= 0 else self.dealing * trades + amount * self.fx


NO_FEES = FeeModel()


@dataclass(frozen=True)
class Month:
    day: date
    """The month-end close this row is valued at."""
    contributed: float
    """Own money in so far."""
    extra: float
    """Extra inflows so far (tax relief, employer money)."""
    fees: float
    """All fees so far."""
    value: float


def project(growth: Series, contributions: Sequence[float], fees: FeeModel = NO_FEES,
            extra: Extra | None = None, start: float = 0.0) -> list[Month]:
    """The pot month by month: each month's contribution (and extra inflow) arrives at the month's
    first close less buying costs, grows to the next close, then the platform fee comes off. One row
    per month of `growth`; months past the end of `contributions` add nothing. Costs above the money
    they are charged on take all of it and no more, so the pot never goes below 0."""
    months = len(growth) - 1
    if len(contributions) > months:
        raise ValueError(f"{len(contributions)} contributions for a series of {max(months, 0)} months")
    pot, paid, added, charged = start, 0.0, 0.0, 0.0
    out = []
    for i in range(months):
        own = contributions[i] if i < len(contributions) else 0.0
        more = extra(i, own) if extra else 0.0
        cost = min(fees.buying(own + more), own + more)
        pot += own + more - cost
        pot *= growth[i + 1][1] / growth[i][1]
        fee = min(fees.platform(pot), pot)
        pot -= fee
        paid, added, charged = paid + own, added + more, charged + cost + fee
        out.append(Month(growth[i + 1][0], paid, added, charged, pot))
    return out


def crossover(percentage: FeeModel, flat: FeeModel) -> float | None:
    """The pot above which the flat platform costs less than the percentage one (their dealing and
    FX fees are taken as equal), or None when it never does (no percentage, or a cap at or below
    the flat fee)."""
    yearly_flat = flat.flat * 12
    if percentage.percent <= 0 or (percentage.cap is not None and percentage.cap <= yearly_flat):
        return None
    return yearly_flat / percentage.percent


def steady(yearly: float, months: int, first: date) -> list[tuple[date, float]]:
    """A series growing `yearly` a year, compounded monthly, from 1.0 on `first`; later points
    are dated the first of each month after it."""
    rate = (1 + yearly) ** (1 / 12)
    out = [(first, 1.0)]
    for i in range(1, months + 1):
        y, m = divmod(first.month - 1 + i, 12)
        out.append((date(first.year + y, m + 1, 1), rate ** i))
    return out


def monthly_returns(series: Series) -> list[float]:
    return [b / a - 1 for (_, a), (_, b) in zip(series, series[1:])]


def drip_schedule(amount: float, months: int, cash_rate: float = 0.0) -> list[float]:
    """`amount` fed in over `months` equal-share tranches; the cash still waiting earns
    `cash_rate` a year, which the later tranches carry in."""
    cash, out = amount, []
    for k in range(months):
        tranche = cash / (months - k)
        out.append(tranche)
        cash = (cash - tranche) * (1 + cash_rate / 12)
    return out
