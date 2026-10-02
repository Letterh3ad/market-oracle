"""Contribution habits on the total-return history (boring-plan spec 4): lump sum against a
12-month drip from every start month, monthly contributions over every window, rebalancing a
two-fund mix, and cash drag, all after the user's platform fees. The growth series is a parameter:
ACWI's total return in GBP (a proxy, from the dividends seam) until fund comparison's series
plug in."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oracle.broker.contributions import (
    BANDS, NONE, YEARLY, CashDrag, MixResult, Rebalance, Spread, Start, align, cash_drag, lump_vs_drip, mix,
    rolling_monthly, spread,
)
from oracle.broker.projection import month_ends
from oracle.services.context import AppContext
from oracle.services.dividends_service import total_return_closes
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fees_service import user_fees

__all__ = ["ContributionTests", "GrowthSeries", "MixLine", "PROXY", "SECOND", "contribution_tests", "growth_series"]

PROXY = "ACWI"
SECOND = "CUKX.L"
"""The default second fund, for a UK home-bias mix: an accumulating FTSE 100 fund in GBP, so its total
return needs no dividend record (Yahoo's list for the distributing ISF.L starts in 2013)."""
MIN_MONTHS = 13


@dataclass(frozen=True)
class GrowthSeries:
    symbol: str
    label: str
    kind: str
    """total return or price only."""
    points: tuple[tuple[date, float], ...]
    """Month-end closes in the base currency."""


def growth_series(ctx: AppContext, symbol: str = PROXY) -> GrowthSeries:
    """A listing's stored closes with dividends reinvested, in the base currency, at month ends."""
    tr = total_return_closes(ctx, symbol)
    points = tuple(month_ends(tr.closes))
    if len(points) < MIN_MONTHS:
        raise NoPrices(f"{symbol} has {len(points)} month ends stored in {ctx.config.base_currency}; the tests need "
                       f"{MIN_MONTHS} or more (prices and FX for its history)")
    base = ctx.config.base_currency
    label = f"{symbol} {tr.kind} in {base}" + (" (proxy)" if symbol == PROXY else "")
    return GrowthSeries(symbol, label, tr.kind, points)


@dataclass(frozen=True)
class MixLine:
    rule: str
    result: MixResult


@dataclass(frozen=True)
class ContributionTests:
    series: GrowthSeries
    fees: str
    amount: float
    monthly: float
    years: int
    starts: tuple[Start, ...]
    lump: Spread
    """Lump sum's lead over the drip, across every start month."""
    windows: Spread
    """Gain on money paid in, across every `years` window of monthly contributions."""
    window_count: int
    second: GrowthSeries | None
    weight: float
    mixes: tuple[MixLine, ...]
    mix_span: tuple[date, date] | None
    cash: CashDrag
    notes: tuple[str, ...]


def contribution_tests(ctx: AppContext, monthly: float, amount: float | None = None, years: int = 10,
                       growth: GrowthSeries | None = None, second: GrowthSeries | str | None = SECOND,
                       weight: float = 0.75, band: float = 0.05, cash_share: float = 0.05,
                       cash_rate: float = 0.0) -> ContributionTests:
    """Every test on `growth` (ACWI's when None), after the user's platform fees. `amount` is the
    lump (12 months' contributions when None). `second` names or gives the mix's other fund; a
    second fund without stored prices is said in the notes, not raised."""
    if monthly <= 0:
        raise InvalidQuery("the monthly amount must be above 0")
    if not 0 < weight < 1 or not 0 < band < 0.5 or not 0 <= cash_share < 1 or not 1 <= years <= 40:
        raise InvalidQuery("weight from 0 to 1, bands under 50 points, cash share under 1, years 1 to 40")
    series = growth or growth_series(ctx)
    fees, lump = user_fees(ctx), amount if amount is not None else monthly * 12
    points = list(series.points)
    starts = lump_vs_drip(points, lump, fees, cash_rate)
    windows = rolling_monthly(points, monthly, years, fees)
    notes = [_fees_note(ctx)]
    if not windows:
        notes.append(f"{series.symbol}'s {len(points) - 1} months are fewer than {years} years: no monthly window.")
    other, mixes, span = _second(ctx, second, notes), (), None
    if other is not None:
        a, b = align(points, list(other.points))
        rules = (Rebalance(NONE), Rebalance(YEARLY), Rebalance(BANDS, band))
        mixes = tuple(MixLine(r.label, mix(a, b, weight, r, fees, [monthly] * (len(a) - 1))) for r in rules)
        span = (a[0][0], a[-1][0])
    return ContributionTests(series, _fees_label(ctx), lump, monthly, years, tuple(starts),
                             spread([s.lead for s in starts]), spread([w.gain for w in windows]), len(windows),
                             other, weight, mixes, span, cash_drag(points, cash_share, cash_rate), tuple(notes))


def _second(ctx: AppContext, second: GrowthSeries | str | None, notes: list[str]) -> GrowthSeries | None:
    if second is None or isinstance(second, GrowthSeries):
        return second
    try:
        return growth_series(ctx, second)
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
    return (f"After your {ctx.config.plan.platform} platform's fees ({_fees_label(ctx)}). The funds' own costs "
            "(the proxy ETF's TER) are already inside their prices. A drip's waiting cash pays no platform fee and "
            "earns the cash rate. No tax: an ISA.")
