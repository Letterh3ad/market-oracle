"""Contribution habits on the total-return history (boring-plan spec 4): lump sum against a
12-month drip from every start month, monthly contributions over every window, rebalancing a
two-fund mix, and cash drag, all after the user's platform fees. The series are fund comparison's
(02): each fund in GBP, accumulating, after its TER, modelled from a long-history proxy kept in the
research database, cut to whole months."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from oracle.broker.contributions import (
    BANDS, NONE, YEARLY, CashDrag, MixResult, Rebalance, Spread, Start, align, cash_drag, lump_vs_drip, mix,
    rolling_monthly, spread, whole_months,
)
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fees_service import user_fees
from oracle.services.fund_compare_service import FUNDS, FundSeries, fund_series

__all__ = ["ContributionTests", "FUND", "MixLine", "SECOND", "contribution_tests", "plan_series"]

FUND = "all_world"
SECOND = "uk"
"""The mix's default second fund: fund comparison's UK part, so the 75/25 mix is its home-bias mix."""
MIN_MONTHS = 13
RUN_FUNDS = ("run the fund comparison first (Compare funds): it fetches each proxy's history and GBP rates into "
             "the research database")


def plan_series(ctx: AppContext, key: str) -> FundSeries:
    """Fund comparison's monthly series for `key`, cut to whole months; NoPrices says which job
    fetches it."""
    try:
        s = fund_series(ctx, key, monthly=True)
    except NoPrices:
        raise NoPrices(f"no stored history for the {FUNDS[key].label} fund: {RUN_FUNDS}") from None
    points = tuple(whole_months(s.points, ctx.clock.utcnow().date()))
    if len(points) < MIN_MONTHS:
        raise NoPrices(f"the {s.label} fund has {max(len(points) - 1, 0)} whole months stored; the tests need "
                       f"{MIN_MONTHS - 1} or more: {RUN_FUNDS}")
    return replace(s, points=points)


@dataclass(frozen=True)
class MixLine:
    rule: str
    result: MixResult


@dataclass(frozen=True)
class ContributionTests:
    series: FundSeries
    fees: str
    amount: float
    monthly: float
    years: int
    first: date
    last: date
    """The month ends the series covers (whole months only)."""
    months: int
    starts: tuple[Start, ...]
    lump: Spread
    """Lump sum's lead over the drip, across every start month."""
    windows: Spread
    """Gain on money paid in, across every `years` window of monthly contributions."""
    window_count: int
    window_span: tuple[date, date] | None
    """First and last window's start."""
    second: FundSeries | None
    weight: float
    mixes: tuple[MixLine, ...]
    mix_span: tuple[date, date] | None
    cash: CashDrag
    notes: tuple[str, ...]


def contribution_tests(ctx: AppContext, monthly: float, amount: float | None = None, years: int = 10,
                       growth: FundSeries | str = FUND, second: FundSeries | str | None = SECOND,
                       weight: float = 0.75, band: float = 0.05, cash_share: float = 0.05,
                       cash_rate: float = 0.0) -> ContributionTests:
    """Every test on `growth` (a fund comparison key, or a series), after the user's platform fees.
    `amount` is the lump (12 months' contributions when None). `second` names or gives the mix's
    other fund; one without stored history is said in the notes, not raised."""
    lump = amount if amount is not None else monthly * 12
    if monthly <= 0 or lump <= 0:
        raise InvalidQuery("the monthly amount and the lump must be above 0")
    if not 0 < weight < 1 or not 0 < band < 0.5 or not 0 <= cash_share < 1 or not 1 <= years <= 40:
        raise InvalidQuery("weight from 0 to 1, bands under 50 points, cash share under 1, years 1 to 40")
    if cash_rate <= -1:
        raise InvalidQuery("the cash rate must be above -100%")
    series = plan_series(ctx, growth) if isinstance(growth, str) else growth
    fees = user_fees(ctx)
    points = list(series.points)
    starts = lump_vs_drip(points, lump, fees, cash_rate)
    if any(s.lead is None for s in starts):
        raise InvalidQuery(f"dealing and FX fees take the whole of a £{lump / 12:,.2f} drip tranche: "
                           "raise the lump or check the platform's fees")
    windows = rolling_monthly(points, monthly, years, fees)
    notes = [_fees_note(ctx)]
    if not windows:
        notes.append(f"{series.label}'s {len(points) - 1} months are fewer than {years} years: no monthly window.")
    other, mixes, span = _second(ctx, second, notes), (), None
    if other is not None:
        a, b = align(points, list(other.points))
        if len(a) >= MIN_MONTHS:
            rules = (Rebalance(NONE), Rebalance(YEARLY), Rebalance(BANDS, band))
            mixes = tuple(MixLine(r.label, mix(a, b, weight, r, fees, [monthly] * (len(a) - 1))) for r in rules)
            span = (a[0][0], a[-1][0])
        else:
            notes.append(f"No rebalancing test: {series.label} and {other.label} share {len(a)} month ends.")
    return ContributionTests(series, _fees_label(ctx), lump, monthly, years, points[0][0], points[-1][0],
                             len(points) - 1, tuple(starts), spread([s.lead for s in starts]),
                             spread([w.gain for w in windows]), len(windows),
                             (windows[0].start, windows[-1].start) if windows else None, other, weight, mixes, span,
                             cash_drag(points, cash_share, cash_rate), tuple(notes))


def _second(ctx: AppContext, second: FundSeries | str | None, notes: list[str]) -> FundSeries | None:
    if second is None or isinstance(second, FundSeries):
        return second
    try:
        return plan_series(ctx, second)
    except NoPrices as exc:
        notes.append(f"No rebalancing test: {exc}.")
        return None


def _fees_label(ctx: AppContext) -> str:
    f = user_fees(ctx)
    p = ctx.config.plan
    fee = (f"flat £{f.flat:.2f} a month" if p.platform == "flat"
           else f"{f.percent * 100:.2f}% a year" + (f" capped at £{f.cap:.2f}" if f.cap else ""))
    return f"{fee}, dealing £{f.dealing:.2f} per trade, FX {f.fx * 100:.2f}% per buy"


def _fees_note(ctx: AppContext) -> str:
    return (f"After your {ctx.config.plan.platform} platform's fees ({_fees_label(ctx)}). Each fund's own TER is "
            "already taken inside its series (fund comparison's modelling). A drip's waiting cash pays no platform "
            "fee and earns the cash rate. No tax: an ISA.")
