"""`oracle plan drawdowns`, `oracle plan rule` and `oracle plan pending`: the Plan tab's guardrails."""

from __future__ import annotations

import argparse
import textwrap

from oracle.services.context import AppContext
from oracle.services.guardrails_service import (
    PlanChange, PlanChangeResult, cancel_pending, change_plan, drawdown_preview, guardrails,
)


def register(sub: argparse._SubParsersAction) -> None:
    d = sub.add_parser("drawdowns", description="what the worst real falls (2000-02, 2008-09, 2020, 2022) would do "
                                                "to your pot and monthly plan, in GBP on the plan's fund (total "
                                                "return), and how long each took to recover")
    d.add_argument("--pot", type=float, help="your pot in GBP, for this answer only (never stored); default an "
                                             "example GBP 10,000")
    d.set_defaults(handler=run_drawdowns)
    r = sub.add_parser("rule", description="your written no-sell rule; a first rule applies at once, changing or "
                                           "clearing it waits 7 days")
    which = r.add_mutually_exclusive_group()
    which.add_argument("--set", dest="text", help="the rule, in your own words")
    which.add_argument("--clear", action="store_true", help="remove the rule (waits 7 days)")
    r.set_defaults(handler=run_rule)
    p = sub.add_parser("pending", description="the plan change waiting out its 7-day cool-off, and recent ones")
    p.add_argument("--cancel", action="store_true", help="cancel the pending change")
    p.set_defaults(handler=run_pending)


def run_drawdowns(ctx: AppContext, args: argparse.Namespace) -> int:
    p = drawdown_preview(ctx, args.pot)
    pot = f"£{p.pot:,.0f}" + (" (example; give --pot)" if p.example else "")
    print(f"Drawdowns on {p.fund_label} ({p.source}, {p.kind}), pot {pot}"
          + (f", £{p.monthly:,.0f} a month" if p.monthly else ""))
    print(f"{'crash':<8} {'measured on':<16} {'peak':<10} {'trough':<10} {'fall':>7} {'pot at low':>11} "
          f"{'loss':>9} {'down':>5} {'back at peak':<13} {'paying: even':<14}")
    for r in p.rows:
        if r.fall is None:
            print(f"{r.crash.name:<8} {r.note}")
            continue
        f = r.fall
        back = f"{f.recovered} ({r.months_back} mo)" if f.recovered else "not yet"
        even = "" if r.paying is None else (f"{r.paying.even} ({r.months_even} mo)" if r.paying.even else "not yet")
        on = r.fund_label + (" *" if r.stand_in else "")
        print(f"{r.crash.name:<8} {on:<16} {f.peak_day} {f.trough_day} {f.fall:>7.1%} {r.pot_low:>11,.0f} "
              f"{r.pot_loss:>9,.0f} {r.months_down:>3} mo {back:<13} {even}")
        if r.paying:
            print(f"{'':<8} paying in: £{r.paying.paid_at_trough:,.0f} paid by the trough, worth "
                  f"£{r.paying.value_at_trough:,.0f}; at worst £{-r.paying.worst_gap:,.0f} below the money paid in")
    print("Down: peak to low. Months in brackets: from the low. * stand-in: the plan's fund history does not reach "
          "back this far.")
    for note in p.notes:
        print(textwrap.fill(note, 100, initial_indent="- ", subsequent_indent="  "))
    return 0


def run_rule(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.text is not None or args.clear:
        print_result(change_plan(ctx, {"rule": "" if args.clear else args.text}))
    g = guardrails(ctx)
    print(f"No-sell rule: {g.rule or '(not written yet)'}")
    return 0


def run_pending(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.cancel:
        gone = cancel_pending(ctx)
        print(f"Cancelled: {changes(gone)}")
    g = guardrails(ctx)
    if g.pending is None:
        print("Nothing pending.")
    else:
        print(f"Pending, takes effect {g.pending.due:%Y-%m-%d %H:%M} UTC unless cancelled: {changes(g.pending)}")
    if g.history:
        print("Recent:")
        for c in g.history:
            print(f"  {c.state:<9} {c.settled_at:%Y-%m-%d %H:%M} {changes(c)}")
    return 0


def changes(c: PlanChange) -> str:
    return "; ".join(f"{k} {old!r} -> {new!r}" for k, (old, new) in c.changes.items())


def print_result(r: PlanChangeResult) -> None:
    if r.applied:
        print(f"Saved [plan] {', '.join(r.applied)}.")
    if r.pending:
        print(f"Pending until {r.pending.due:%Y-%m-%d %H:%M} UTC (7-day cool-off; `oracle plan pending --cancel` to "
              f"drop it): {changes(r.pending)}")
