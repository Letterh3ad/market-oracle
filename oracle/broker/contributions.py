"""Contribution habits tested on history (boring-plan spec 4): lump sum against a 12-month drip from
every start month, monthly contributions over every window, rebalancing a two-fund mix (none,
yearly, bands) and the cost of cash left uninvested. Numbers in, numbers out, on month-end
total-return closes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median

from oracle.broker.projection import NO_FEES, FeeModel, Series, drip_schedule, project

NONE, YEARLY, BANDS, MONTHLY = "none", "yearly", "bands", "monthly"
DRIP_MONTHS = 12


@dataclass(frozen=True)
class Start:
    start: date
    end: date
    """The close both are valued at: the drip's last tranche has had a month in the market."""
    lump: float
    drip: float

    @property
    def lead(self) -> float | None:
        """How far the lump sum ends ahead of the drip, as a share of the drip; None when costs
        took the whole drip."""
        return self.lump / self.drip - 1 if self.drip > 0 else None


def lump_vs_drip(series: Series, amount: float, fees: FeeModel = NO_FEES, cash_rate: float = 0.0,
                 months: int = DRIP_MONTHS) -> list[Start]:
    """`amount` invested at once against fed in over `months`, from every start month with a full
    drip after it. The drip's waiting cash earns `cash_rate` a year."""
    tranches = drip_schedule(amount, months, cash_rate)
    out = []
    for s in range(len(series) - months):
        window = series[s:s + months + 1]
        lump = project(window, [amount], fees)[-1].value
        drip = project(window, tranches, fees)[-1].value
        out.append(Start(window[0][0], window[-1][0], lump, drip))
    return out


@dataclass(frozen=True)
class Spread:
    count: int
    wins: float
    """Share above zero."""
    median: float
    p10: float
    p90: float
    worst: float
    best: float


def spread(values: Sequence[float]) -> Spread:
    ordered = sorted(values)
    if not ordered:
        return Spread(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    n = len(ordered)
    return Spread(n, sum(v > 0 for v in ordered) / n, median(ordered), _quantile(ordered, 0.1),
                  _quantile(ordered, 0.9), ordered[0], ordered[-1])


def _quantile(ordered: Sequence[float], q: float) -> float:
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


@dataclass(frozen=True)
class Window:
    start: date
    end: date
    contributed: float
    value: float
    fees: float

    @property
    def gain(self) -> float:
        return self.value / self.contributed - 1


def rolling_monthly(series: Series, monthly: float, years: int, fees: FeeModel = NO_FEES) -> list[Window]:
    """`monthly` paid in every month for `years`, from every start month with that many after it."""
    months = years * 12
    out = []
    for s in range(len(series) - months):
        window = series[s:s + months + 1]
        end = project(window, [monthly] * months, fees)[-1]
        out.append(Window(window[0][0], end.day, end.contributed, end.value, end.fees))
    return out


@dataclass(frozen=True)
class Rebalance:
    kind: str
    """NONE, YEARLY (every twelfth month end), BANDS (any month end the first fund's weight is
    more than `points` off target) or MONTHLY."""
    points: float = 0.05

    @property
    def label(self) -> str:
        return f"{round(self.points * 100)}-point bands" if self.kind == BANDS else self.kind


@dataclass(frozen=True)
class MixResult:
    value: float
    contributed: float
    fees: float
    trades: int
    rebalanced: tuple[date, ...]
    weight: float
    """The first fund's share at the end."""
    worst_fall: float
    """On the time-weighted index, so money paid in never hides a fall."""
    growth: float
    """The time-weighted index's growth (1.5 is up 50%)."""
    path: tuple[tuple[date, float], ...]


def mix(a: Series, b: Series, weight: float, rule: Rebalance, fees: FeeModel = NO_FEES,
        contributions: Sequence[float] = (), start: float = 0.0) -> MixResult:
    """Two funds on the same month ends, `weight` in the first. Money in is split by the target
    weights; a rebalance sells the heavy fund and buys the light one at the month end, paying
    dealing on both trades and FX on the buy (no tax: an ISA)."""
    held = [start * weight, start * (1 - weight)]
    targets = (weight, 1 - weight)
    paid = charged = 0.0
    trades, rebalanced, path = 0, [], []
    index = peak = 1.0
    worst = 0.0
    for i in range(len(a) - 1):
        own = contributions[i] if i < len(contributions) else 0.0
        for k in (0, 1):
            put = own * targets[k]
            cost = min(fees.buying(put), put)
            held[k] += put - cost
            charged += cost
        paid += own
        before = sum(held)
        held[0] *= a[i + 1][1] / a[i][1]
        held[1] *= b[i + 1][1] / b[i][1]
        pot = sum(held)
        fee = min(fees.platform(pot), pot)
        held = [h - fee * h / pot for h in held] if pot > 0 else held
        charged += fee
        if before > 0:
            index *= sum(held) / before
            peak = max(peak, index)
            worst = max(worst, 1 - index / peak)
        if _due(rule, i + 1, held, weight):
            pot = sum(held)
            moved = abs(held[0] - pot * weight)
            cost = min(fees.dealing * 2 + moved * fees.fx, pot)
            pot -= cost
            held = [pot * weight, pot * (1 - weight)]
            charged += cost
            trades += 2
            rebalanced.append(a[i + 1][0])
        path.append((a[i + 1][0], sum(held)))
    pot = sum(held)
    return MixResult(pot, paid, charged, trades, tuple(rebalanced), held[0] / pot if pot else weight, worst, index,
                     tuple(path))


def _due(rule: Rebalance, month: int, held: Sequence[float], weight: float) -> bool:
    pot = sum(held)
    if pot <= 0 or weight in (0.0, 1.0):
        return False
    drift = abs(held[0] / pot - weight)
    if drift < 1e-9:
        return False
    if rule.kind == MONTHLY:
        return True
    if rule.kind == YEARLY:
        return month % 12 == 0
    if rule.kind == BANDS:
        return drift > rule.points
    return False


def whole_months(series: Series, today: date) -> list[tuple[date, float]]:
    """Month-end points cut to whole months: a first point inside the same month as the next (a
    series' first day) and a last point in `today`'s month (a month still running) are dropped."""
    out = list(series)
    if len(out) > 1 and (out[0][0].year, out[0][0].month) == (out[1][0].year, out[1][0].month):
        out = out[1:]
    if out and (out[-1][0].year, out[-1][0].month) == (today.year, today.month):
        out = out[:-1]
    return out


def align(a: Series, b: Series) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    """Both series cut to the calendar months they share, each keeping its own close."""
    by_a = {(d.year, d.month): (d, c) for d, c in a}
    by_b = {(d.year, d.month): (d, c) for d, c in b}
    shared = sorted(by_a.keys() & by_b.keys())
    return [by_a[m] for m in shared], [by_b[m] for m in shared]


@dataclass(frozen=True)
class CashDrag:
    share: float
    cash_rate: float
    invested: float
    """Growth of 1 fully invested."""
    held: float
    """Growth of 1 with `share` kept in cash, topped back up each month end."""
    given_up: float
    """Yearly return given up (0.004 is 0.4 points)."""


def cash_drag(series: Series, share: float, cash_rate: float = 0.0) -> CashDrag:
    cash = [(d, (1 + cash_rate / 12) ** i) for i, (d, _) in enumerate(series)]
    held = mix(series, cash, 1 - share, Rebalance(MONTHLY), start=1.0).value
    invested = series[-1][1] / series[0][1]
    years = (len(series) - 1) / 12
    return CashDrag(share, cash_rate, invested, held, invested ** (1 / years) - held ** (1 / years))
