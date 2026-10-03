"""The "your plan" summary (boring-plan spec 6): which fund, in which wrapper, on which platform, how
much a month, and whether to use the trend overlay, each with its reason in plain words. Read from
the kept fund comparison and trend test and the [plan] settings; what is not known yet is listed as
missing, never raised."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.broker.uk_rates import ISA_ALLOWANCE
from oracle.config import PlanConfig
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.fees_service import ASSUMED_GROWTH, fee_drag
from oracle.services.fund_compare_service import FUNDS, FundComparison, FundRow, fund_comparison
from oracle.services.trend_service import TrendTest, trend_tests

__all__ = ["DEFAULT_FUND", "ISA_ALLOWANCE", "PICKABLE", "Missing", "PlanLine", "PlanSummary", "check_choice",
           "default_pick", "plan_fund", "plan_summary"]

DEFAULT_FUND = "all_world"
PICKABLE = tuple(k for k, f in FUNDS.items() if f.compared)
"""Funds a plan can hold; the UK part is shown only inside the mix."""
HORIZON = 25


@dataclass(frozen=True)
class PlanLine:
    name: str
    label: str
    value: str
    reason: str
    known: bool
    """False while it waits on something in `missing`."""


@dataclass(frozen=True)
class Missing:
    step: str
    """What to do: funds, trend, platform, monthly or fund (a saved pick the app does not know)."""
    text: str


@dataclass(frozen=True)
class PlanSummary:
    lines: tuple[PlanLine, ...]
    missing: tuple[Missing, ...]
    fund: str
    picked: bool
    """True when `fund` is the user's own pick, False when it is the numbers' default."""
    default_fund: str
    monthly: float
    overlay: bool
    funds: tuple[tuple[str, str], ...]
    """The pickable funds, key and label."""


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def default_pick(rows: tuple[FundRow, ...]) -> tuple[str, str]:
    """All-world unless a fund at most as US-heavy beats it on both yearly return and worst fall over
    the shared history: a more concentrated fund's lead is a bet on that market, not a better spread."""
    by_key = {r.key: r for r in rows if r.compared}
    base = by_key.get(DEFAULT_FUND)
    if base is None:
        return DEFAULT_FUND, "All-world is the widest spread of shares; it is not in the kept comparison."
    beaters = sorted((r for r in by_key.values() if r.key != DEFAULT_FUND and r.us_share <= base.us_share
                      and r.cagr > base.cagr and r.max_fall > base.max_fall), key=lambda r: -r.cagr)
    rule = ("The pick moves off all-world only if a fund no more US-heavy beats it on both yearly return and "
            "worst fall.")
    if beaters:
        b = beaters[0]
        return b.key, (f"{FUNDS[b.key].label} beat all-world on both counts while no more US-heavy "
                       f"({b.us_share:.0%} US): {_pct(b.cagr)} a year against {_pct(base.cagr)}, worst fall "
                       f"{b.max_fall:.1%} against {base.max_fall:.1%}. {rule}")
    reason = (f"All-world is the widest spread ({base.us_share:.0%} US): {_pct(base.cagr)} a year after its "
              f"{base.ter:.2%} charge, worst fall {base.max_fall:.1%}.")
    ahead = [r for r in by_key.values() if r.key != DEFAULT_FUND and r.cagr > base.cagr]
    if ahead:
        named = ", ".join(f"{FUNDS[r.key].label} {_pct(r.cagr)} ({r.us_share:.0%} US)"
                          for r in sorted(ahead, key=lambda r: -r.cagr))
        reason += (f" More US-heavy funds returned more over this window ({named}): that lead is the US "
                   "market's run, not a reason to drop the rest of the world.")
    return DEFAULT_FUND, f"{reason} {rule}"


def _buy(key: str) -> str:
    f = FUNDS[key]
    if f.parts:
        return " + ".join(f"{w:.0%} {FUNDS[k].real_label}" for k, w in f.parts)
    return f.real_label or f.label


def _fund_line(plan: PlanConfig, c: FundComparison | None, missing: list[Missing]) -> tuple[PlanLine, str, bool, str]:
    if c is None:
        default, why = DEFAULT_FUND, "All-world is the diversified default; it has not been checked against the numbers yet."
        missing.append(Missing("funds", "Run Compare funds (Fund comparison card) to check the fund against the numbers."))
    else:
        default, why = default_pick(c.rows)
        why += f" Numbers from proxies, {c.start} to {c.end}."
    pick = plan.fund.strip().lower()
    if pick and pick not in PICKABLE:
        missing.append(Missing("fund", f"The saved fund {plan.fund!r} is not one this app compares: pick one under "
                                       "Your plan."))
        pick = ""
    key = pick or default
    reason = f"Your pick. The numbers' default is {FUNDS[default].label}: {why}" if pick else why
    line = PlanLine("fund", "Fund", f"{FUNDS[key].label}: {_buy(key)}", reason, c is not None)
    return line, key, bool(pick), default


def _wrapper_line(plan: PlanConfig) -> PlanLine:
    reason = ("Gains and dividends inside it are free of UK tax and the money can come out any time, so the plan "
              "never has to sell for tax reasons.")
    if plan.monthly:
        yearly = plan.monthly * 12
        reason += f" £{yearly:,.0f} a year of the £{ISA_ALLOWANCE:,} allowance."
        if yearly > ISA_ALLOWANCE:
            reason += " That is over the allowance: the rest would go outside the ISA."
    return PlanLine("wrapper", "Wrapper", "Stocks and Shares ISA", reason, True)


def _platform_line(ctx: AppContext, plan: PlanConfig, missing: list[Missing]) -> PlanLine:
    flat = plan.platform == "flat"
    fee = (f"£{plan.flat_fee:.2f} a month" if flat else f"{plan.percent_fee:.2f}% a year"
           + (f", capped at £{plan.percent_cap:,.2f} a year" if plan.percent_cap else ""))
    value = f"{'Flat' if flat else 'Percentage'} fee, {fee}"
    if plan.dealing_fee or plan.fx_fee:
        value += f"; dealing £{plan.dealing_fee:.2f}, FX {plan.fx_fee:.2f}%"
    if not (plan.flat_fee if flat else plan.percent_fee):
        missing.append(Missing("platform", "Enter your platform's prices (Fees and contributions card, Your platform)."))
        return PlanLine("platform", "Platform", f"{'Flat' if flat else 'Percentage'} fee, not entered yet",
                        "The fee your platform charges sets how much of the pot it takes each year.", False)
    if not (plan.flat_fee and plan.percent_fee):
        return PlanLine("platform", "Platform", value, "Enter both a percentage and a flat price to see the pot size "
                        "where flat costs less.", True)
    d = fee_drag(ctx, plan.monthly, HORIZON, ASSUMED_GROWTH)
    if d.crossover is None:
        reason = "At these prices flat never costs less than percentage, so percentage is the cheaper type."
    else:
        reason = f"At these prices flat costs less than percentage once the pot is above £{d.crossover:,.0f}"
        if plan.monthly and d.crossover_year:
            reason += (f"; £{plan.monthly:,.0f} a month passes that in year {d.crossover_year} "
                       f"(at an assumed {ASSUMED_GROWTH:.0%} a year)")
        reason += "." + (" Move to a flat-fee platform once the pot is past it." if not flat else
                         " Below it a percentage platform would cost less.")
    return PlanLine("platform", "Platform", value, reason, True)


def _monthly_line(plan: PlanConfig, missing: list[Missing]) -> PlanLine:
    if not plan.monthly:
        missing.append(Missing("monthly", "Set your monthly amount (Your plan card)."))
        return PlanLine("monthly", "Monthly", "not set", "Paid in each month whatever the market does.", False)
    return PlanLine("monthly", "Monthly", f"£{plan.monthly:,.0f} a month",
                    "Paid in each month whatever the market does; the contribution tests show what that did over "
                    "every window in the history.", True)


def _overlay_line(t: TrendTest | None, missing: list[Missing]) -> PlanLine:
    if t is None:
        missing.append(Missing("trend", "Run the trend test (Trend overlay test card) to decide on the overlay."))
        return PlanLine("overlay", "Trend overlay", "No (not tested yet)", "Until the pre-registered test passes, "
                        "the plan holds the fund through falls.", False)
    w = t.confirm
    figures = (f"since {w.start} it cut the worst fall by {w.fall_cut:.0%} (needs 33%) and gave up "
               f"{w.given_up * 100:.2f} points a year (at most 1.00 allowed)")
    if t.passed:
        return PlanLine("overlay", "Trend overlay", "Yes", f"The pre-registered test passed: {figures}. Move to cash "
                        "when the fund closes a month below its 10-month average, back when above.", True)
    return PlanLine("overlay", "Trend overlay", "No", f"The pre-registered test failed: {figures}. Hold the fund "
                    "through falls.", True)


def _rule_line(plan: PlanConfig, missing: list[Missing]) -> PlanLine:
    if not plan.rule:
        missing.append(Missing("rule", "Write your no-sell rule (Guardrails card)."))
        return PlanLine("rule", "No-sell rule", "not written", "Written while calm, it is what you read in a fall "
                        "instead of selling.", False)
    return PlanLine("rule", "No-sell rule", plan.rule, "Your own words, written while calm: read them before selling "
                    "anything in a fall. Changing them waits 7 days.", True)


def plan_fund(ctx: AppContext) -> str:
    """The fund the plan holds: the user's pick, else the numbers' default."""
    pick = ctx.config.plan.fund.strip().lower()
    if pick in PICKABLE:
        return pick
    c = fund_comparison(ctx)
    return default_pick(c.rows)[0] if c else DEFAULT_FUND


def plan_summary(ctx: AppContext) -> PlanSummary:
    plan = ctx.config.plan
    missing: list[Missing] = []
    fund, key, picked, default = _fund_line(plan, fund_comparison(ctx), missing)
    t = trend_tests(ctx)
    lines = (fund, _wrapper_line(plan), _platform_line(ctx, plan, missing), _monthly_line(plan, missing),
             _overlay_line(t, missing), _rule_line(plan, missing))
    order = ("funds", "fund", "platform", "monthly", "trend", "rule")
    return PlanSummary(lines, tuple(sorted(missing, key=lambda m: order.index(m.step))), key, picked, default,
                       plan.monthly, bool(t and t.passed), tuple((k, FUNDS[k].label) for k in PICKABLE))


def check_choice(values: dict[str, object]) -> dict[str, object]:
    """`values` with `fund` checked against the pickable funds ("default" as "") and `rule` trimmed."""
    values = dict(values)
    if isinstance(values.get("rule"), str):
        values["rule"] = values["rule"].strip()
    if "fund" in values:
        fund = values["fund"]
        if not isinstance(fund, str):
            raise InvalidQuery(f"fund must be a fund name, got {fund!r}")
        fund = fund.strip().lower()
        fund = "" if fund == "default" else fund
        if fund and fund not in PICKABLE:
            raise InvalidQuery(f"fund must be one of {', '.join(PICKABLE)} or default, got {fund!r}")
        values["fund"] = fund
    return values
