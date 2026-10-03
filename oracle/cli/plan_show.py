"""`oracle plan show` and `oracle plan set`: the "your plan" summary and the [plan] settings behind it."""

from __future__ import annotations

import argparse
import textwrap

from oracle.cli.plan_guard import print_result
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.guardrails_service import change_plan, settle
from oracle.services.plan_service import PICKABLE, PlanSummary, plan_summary

COMMANDS = {"funds": "oracle plan funds", "trend": "oracle plan trend", "platform": "oracle plan set --percent-fee PCT, or --platform flat --flat-fee GBP",
            "monthly": "oracle plan set --monthly GBP", "fund": "oracle plan set --fund NAME",
            "rule": 'oracle plan rule --set "TEXT"'}
SETTERS = (("fund", str, f"the fund you hold: {', '.join(PICKABLE)}, or default for the numbers' pick"),
           ("monthly", float, "GBP paid in each month"),
           ("platform", str, "which fee you pay: percentage or flat"),
           ("percent_fee", float, "the percentage platform's fee, %% a year"),
           ("percent_cap", float, "GBP a year the percentage fee stops at; 0 is no cap"),
           ("flat_fee", float, "the flat platform's fee, GBP a month"),
           ("dealing_fee", float, "GBP per buy or sell"),
           ("fx_fee", float, "%% of each buy taken for currency conversion"))


def register(sub: argparse._SubParsersAction) -> None:
    show = sub.add_parser("show", description="your plan: the fund, the wrapper, the platform, the monthly amount and "
                                              "the trend overlay yes or no, each with its reason, from the kept "
                                              "fund comparison and trend test and the [plan] settings")
    show.set_defaults(handler=run_show)
    s = sub.add_parser("set", description="save [plan] settings in one checked write; keys not given keep their "
                                          "values. A change to a value already set waits 7 days (oracle plan "
                                          "pending)")
    for key, kind, text in SETTERS:
        s.add_argument(f"--{key.replace('_', '-')}", dest=key, type=kind, help=text)
    s.set_defaults(handler=run_set)


def run_show(ctx: AppContext, args: argparse.Namespace) -> int:
    settle(ctx)
    print_summary(plan_summary(ctx))
    return 0


def print_summary(s: PlanSummary) -> None:
    print("Your plan")
    for line in s.lines:
        print(f"  {line.label + ':':<15} {line.value}")
        print(textwrap.fill(line.reason, 100, initial_indent=" " * 18, subsequent_indent=" " * 18))
    if s.missing:
        print("Missing:")
        for m in s.missing:
            print(textwrap.fill(m.text, 100, initial_indent="- ", subsequent_indent="  "))
            print(f"  CLI: {COMMANDS[m.step]}")


def run_set(ctx: AppContext, args: argparse.Namespace) -> int:
    values = {key: getattr(args, key) for key, _, _ in SETTERS if getattr(args, key) is not None}
    if not values:
        raise InvalidQuery("nothing to set: give at least one of " + ", ".join(
            f"--{k.replace('_', '-')}" for k, _, _ in SETTERS))
    print_result(change_plan(ctx, values))
    print_summary(plan_summary(ctx))
    return 0
