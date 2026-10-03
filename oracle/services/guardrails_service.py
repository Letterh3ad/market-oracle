"""Guardrails for the real-money plan (boring-plan 08): what the worst real falls would do to the pot
and the monthly plan, the user's own no-sell rule, and a 7-day cool-off on any change to a plan value
already set. The cool-off lives in the live database; a due change is applied by whichever reader
(GUI or CLI) settles first."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from oracle.broker.drawdowns import Fall, Paying, covers, keep_paying, months, worst_fall
from oracle.config import PLAN_PARTS, ConfigError, PlanConfig, check_plan
from oracle.services.context import AppContext
from oracle.services.dividends_service import TOTAL_RETURN
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fund_compare_service import FUNDS, FundSeries, fund_series
from oracle.services.plan_service import check_choice, plan_fund
from oracle.services.settings_service import reload_live_settings, set_plan
from oracle.store.plan_changes import PlanChange, PlanChangeRepo

__all__ = ["COOL_OFF", "CRASHES", "Crash", "Drawdown", "DrawdownPreview", "EXAMPLE_POT", "Guardrails", "PlanChange",
           "PlanChangeResult", "cancel_pending", "change_plan", "drawdown_preview", "guardrails", "settle"]

COOL_OFF = timedelta(days=7)
EXAMPLE_POT = 10_000.0
STAND_IN = "sp500"
"""^SP500TR in GBP reaches back to 1988, so it stands in where the plan's fund proxy starts later."""


@dataclass(frozen=True)
class PlanChangeResult:
    applied: tuple[str, ...]
    """Keys saved at once: a blank part filled for the first time."""
    pending: PlanChange | None
    """The pending change after this request, if any."""
    plan: PlanConfig


@dataclass(frozen=True)
class Guardrails:
    rule: str
    pending: PlanChange | None
    history: tuple[PlanChange, ...]
    cool_off_days: int


def settle(ctx: AppContext) -> PlanChange | None:
    """Apply the pending change once its 7 days are up; the settled change, or None. Every plan reader
    calls it first."""
    _reload(ctx)
    repo = PlanChangeRepo(ctx.conn)
    p = repo.pending()
    now = ctx.clock.utcnow()
    if p is None or p.due > now:
        return None
    try:
        set_plan(ctx, {k: to for k, (_, to) in p.changes.items()})
    except InvalidQuery as exc:
        return repo.settle(p.id, "refused", now, str(exc))
    return repo.settle(p.id, "applied", now)


def _reload(ctx: AppContext) -> None:
    """Take [plan] as oracle.toml has it now, so a CLI write reaches a running GUI before it decides."""
    if ctx.config_source is not None and ctx.config_source.is_file():
        reload_live_settings(ctx)


def change_plan(ctx: AppContext, values: dict[str, object]) -> PlanChangeResult:
    """Ask for [plan] changes. A part (see PLAN_PARTS) still at its defaults is filled at once; a change
    to a set part joins the one pending change and restarts its 7 days. A key given at its current value
    changes nothing, pending included: only cancelling drops a pending change, so a form that sends every
    field never undoes another field's change. Every value is checked now, not when it falls due."""
    settle(ctx)
    values = check_choice(values)
    repo = PlanChangeRepo(ctx.conn)
    pending = repo.pending()
    before = dict(pending.changes) if pending else {}
    plan, blank = asdict(ctx.config.plan), asdict(PlanConfig())
    try:
        check_plan({**plan, **{k: to for k, (_, to) in before.items()}, **values})
    except ConfigError as exc:
        raise InvalidQuery(str(exc)) from None
    now, part_of = ctx.clock.utcnow(), {k: part for part in PLAN_PARTS for k in part}
    at_once, after = {}, {k: v for k, v in before.items()}
    for key, value in values.items():
        part = part_of.get(key)
        if value == plan[key]:
            continue
        if part is None or all(plan[k] == blank[k] for k in part):
            at_once[key] = value
        else:
            after[key] = (plan[key], value)
    if at_once:
        set_plan(ctx, at_once)
    if after == before:
        return PlanChangeResult(tuple(at_once), pending, ctx.config.plan)
    fresh = any(before.get(k) != v for k, v in after.items())
    made, due = (now, now + COOL_OFF) if fresh or pending is None else (pending.made_at, pending.due)
    return PlanChangeResult(tuple(at_once), repo.put_pending(after, made, due), ctx.config.plan)


def cancel_pending(ctx: AppContext) -> PlanChange:
    settle(ctx)
    repo = PlanChangeRepo(ctx.conn)
    p = repo.pending()
    if p is None:
        raise InvalidQuery("nothing pending to cancel")
    return repo.settle(p.id, "cancelled", ctx.clock.utcnow(), "cancelled")


def guardrails(ctx: AppContext) -> Guardrails:
    settle(ctx)
    repo = PlanChangeRepo(ctx.conn)
    return Guardrails(ctx.config.plan.rule, repo.pending(), tuple(repo.history()), COOL_OFF.days)


@dataclass(frozen=True)
class Crash:
    name: str
    label: str
    start: date
    end: date
    """The window the worst fall's peak and trough are looked for in."""


CRASHES = (
    Crash("2000-02", "Dot-com bust", date(2000, 1, 1), date(2003, 6, 30)),
    Crash("2008-09", "Financial crisis", date(2007, 6, 1), date(2009, 12, 31)),
    Crash("2020", "Covid crash", date(2020, 1, 1), date(2020, 12, 31)),
    Crash("2022", "Rates and inflation", date(2021, 11, 1), date(2023, 6, 30)),
)


@dataclass(frozen=True)
class Drawdown:
    crash: Crash
    fund: str
    """The fund key whose series measured it; "" when none covers the window."""
    fund_label: str
    stand_in: bool
    fall: Fall | None
    months_down: int | None
    """Peak to trough."""
    months_back: int | None
    """Trough to back at the peak; None while not back yet."""
    pot_low: float | None
    pot_loss: float | None
    paying: Paying | None
    """The pot that kept paying in monthly; None with no monthly amount set."""
    months_even: int | None
    """Trough to the pot back at the money paid in, as `months_back` is to the price back at its peak."""
    note: str


@dataclass(frozen=True)
class DrawdownPreview:
    fund: str
    fund_label: str
    source: str
    kind: str
    pot: float
    example: bool
    """True when no pot was given and EXAMPLE_POT stands in."""
    monthly: float
    rows: tuple[Drawdown, ...]
    notes: tuple[str, ...]


def drawdown_preview(ctx: AppContext, pot: float | None = None) -> DrawdownPreview:
    """Each crash in CRASHES on the plan's fund (GBP, total return, after its TER), or on the S&P 500
    where the fund's proxy starts too late. `pot` is never stored."""
    settle(ctx)
    if pot is not None and not (0 < pot < math.inf):
        raise InvalidQuery(f"the pot must be above 0, got {pot!r}")
    key, monthly = plan_fund(ctx), ctx.config.plan.monthly
    series: dict[str, FundSeries] = {}
    for k in dict.fromkeys((key, STAND_IN)):
        try:
            series[k] = fund_series(ctx, k)
        except NoPrices:
            pass
    if not series:
        raise NoPrices("no stored history for the plan's fund or the S&P 500: run Compare funds (Fund comparison "
                       "card) first")
    amount = EXAMPLE_POT if pot is None else pot
    rows = tuple(_drawdown(c, key, series, amount, monthly) for c in CRASHES)
    own = series.get(key)
    shown = own or series[STAND_IN]
    return DrawdownPreview(key, FUNDS[key].label, shown.source, shown.kind, amount, pot is None, monthly, rows,
                           _notes(key, own, rows, monthly))


def _drawdown(c: Crash, key: str, series: dict[str, FundSeries], pot: float, monthly: float) -> Drawdown:
    for k, s in series.items():
        if not covers(s.points, c.start, c.end):
            continue
        fall = worst_fall(s.points, c.start, c.end)
        if fall is None:
            return Drawdown(c, k, s.label, k != key, None, None, None, None, None, None, None,
                            f"{s.label} did not fall from {c.start} to {c.end}.")
        paying = keep_paying(s.points, fall, pot, monthly) if monthly else None
        return Drawdown(c, k, s.label, k != key, fall, months(fall.peak_day, fall.trough_day),
                        months(fall.trough_day, fall.recovered) if fall.recovered else None, pot * (1 + fall.fall),
                        pot * fall.fall, paying,
                        months(fall.trough_day, paying.even) if paying and paying.even else None, "")
    spans = "; ".join(f"{s.label} from {s.points[0][0]} to {s.points[-1][0]}" for s in series.values())
    return Drawdown(c, "", "", False, None, None, None, None, None, None, None,
                    f"No stored series spans {c.start} to {c.end} ({spans}).")


def _notes(key: str, own: FundSeries | None, rows: tuple[Drawdown, ...], monthly: float) -> tuple[str, ...]:
    kind = own.kind if own else TOTAL_RETURN
    measure = "total return (dividends reinvested)" if kind == TOTAL_RETURN else kind
    notes = [f"In GBP, {measure}, after the fund's charge (TER), before platform fees; from proxies, not the "
             "real fund. Back at the peak means in GBP, not counting inflation."]
    if own is None:
        notes.append(f"{FUNDS[key].label} has no stored history: run Compare funds. Every row is the S&P 500.")
    if any(r.stand_in for r in rows):
        notes.append(f"Where {FUNDS[key].label}'s proxy starts too late, the S&P 500 ({FUNDS[STAND_IN].proxy.symbol} "
                     "in GBP) stands in. It is 100% US, so its falls are not the same as a wider fund's.")
    if monthly:
        notes.append(f"Paying in: the pot bought at the peak, then £{monthly:,.0f} at each month's last trading day. "
                     "Even is when the pot is back to the money paid in; buying through the fall gets there before "
                     "the price is back.")
    else:
        notes.append("Set your monthly amount (Your plan) to see what paying in through each fall does.")
    return tuple(notes)
