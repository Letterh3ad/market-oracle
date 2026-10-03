"""The fee calculator (boring-plan spec 3): the user's platform from `[plan]`, the pot size where
a flat fee beats a percentage one, and what fees take over a horizon at an assumed growth rate.
`project` is the one contribution and fee projection every plan panel calls (ticket 09 adds its
tax relief and employer money through `extra`)."""

from __future__ import annotations

from dataclasses import dataclass
from oracle.broker.projection import NO_FEES, Extra, FeeModel, Month, Series, crossover, project, steady
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery

__all__ = ["ASSUMED_GROWTH", "Extra", "FeeDrag", "FeeLine", "FeeModel", "Month", "Series", "fee_drag",
           "flat_platform", "percentage_platform", "project", "user_fees"]

ASSUMED_GROWTH = 0.05
"""Yearly growth for a projection forward when the user gives none: an assumption, said as one."""
MAX_YEARS = 60


def percentage_platform(ctx: AppContext) -> FeeModel:
    p = ctx.config.plan
    return FeeModel(percent=p.percent_fee / 100, cap=p.percent_cap or None, dealing=p.dealing_fee,
                    fx=p.fx_fee / 100)


def flat_platform(ctx: AppContext) -> FeeModel:
    p = ctx.config.plan
    return FeeModel(flat=p.flat_fee, dealing=p.dealing_fee, fx=p.fx_fee / 100)


def user_fees(ctx: AppContext) -> FeeModel:
    """The platform the user says they pay (`[plan] platform`)."""
    return flat_platform(ctx) if ctx.config.plan.platform == "flat" else percentage_platform(ctx)


@dataclass(frozen=True)
class FeeLine:
    name: str
    label: str
    value: float
    fees: float
    drag: float
    """What the fees cost by the end: the no-fee pot less this one (their growth lost included)."""
    yearly: tuple[tuple[int, float], ...]
    """(year, pot at that year's end)."""


@dataclass(frozen=True)
class FeeDrag:
    monthly: float
    years: int
    growth: float
    start: float
    contributed: float
    crossover: float | None
    """The pot above which the flat platform costs less; None when it never does."""
    crossover_year: int | None
    """The first year end the percentage line's pot is past it."""
    using: str
    lines: tuple[FeeLine, ...]
    notes: tuple[str, ...]


def fee_drag(ctx: AppContext, monthly: float, years: int, growth: float = ASSUMED_GROWTH,
             start: float = 0.0) -> FeeDrag:
    """`monthly` paid in for `years` on both of the user's platforms and on none, at `growth` a year."""
    if monthly < 0 or start < 0:
        raise InvalidQuery("the monthly amount and the starting pot must be 0 or more")
    if not 1 <= years <= MAX_YEARS:
        raise InvalidQuery(f"years must be from 1 to {MAX_YEARS}")
    if not -0.5 < growth < 0.5:
        raise InvalidQuery("growth is a yearly share, e.g. 0.05 for 5%")
    series = steady(growth, years * 12, ctx.clock.utcnow().date())
    pct, flat = percentage_platform(ctx), flat_platform(ctx)
    paths = {name: project(series, [monthly] * (years * 12), fees, start=start)
             for name, fees in (("none", NO_FEES), ("percentage", pct), ("flat", flat))}
    labels = {"none": "No fees", "percentage": _pct_label(pct), "flat": f"Flat £{flat.flat:.2f} a month"}
    plain = paths["none"]
    lines = tuple(_line(name, labels[name], path, plain) for name, path in paths.items())
    cross = crossover(pct, flat)
    year = next((y for y, v in lines[1].yearly if cross is not None and v > cross), None)
    return FeeDrag(monthly, years, growth, start, plain[-1].contributed, cross, year, ctx.config.plan.platform,
                   lines, _notes(ctx, growth))


def _line(name: str, label: str, path: list[Month], plain: list[Month]) -> FeeLine:
    end = path[-1]
    yearly = tuple((i // 12 + 1, m.value) for i, m in enumerate(path) if (i + 1) % 12 == 0)
    return FeeLine(name, label, end.value, end.fees, plain[-1].value - end.value, yearly)


def _pct_label(m: FeeModel) -> str:
    cap = f", capped at £{m.cap:.2f} a year" if m.cap else ""
    return f"Percentage {m.percent * 100:.2f}% a year{cap}"


def _notes(ctx: AppContext, growth: float) -> tuple[str, ...]:
    p = ctx.config.plan
    out = [f"Growth is an assumption: {growth:.1%} a year, compounded monthly, after fund costs (TER). History "
           "says nothing about the next decades' rate; fee drag scales with it.",
           f"Dealing £{p.dealing_fee:.2f} per buy and FX {p.fx_fee:.2f}% of each buy apply to both platforms; "
           "the crossover compares the platform fees only."]
    if not any((p.percent_fee, p.flat_fee, p.dealing_fee, p.fx_fee)):
        out.append("No platform fees are set: enter your platform's prices in Settings, [plan].")
    return tuple(out)

