"""`oracle plan wrappers`: ISA against workplace pension (and the Lifetime ISA when on), and where
the next pound goes. Flags save your [wrappers] numbers first."""

from __future__ import annotations

import argparse
import textwrap

from oracle.services.context import AppContext
from oracle.services.fees_service import ASSUMED_GROWTH
from oracle.services.wrappers_service import PER, WrapperComparison, check_growth, save_wrappers, wrapper_comparison

COMMANDS = {"salary": "oracle plan wrappers --salary GBP --employee-pct PCT --employer-pct PCT",
            "born": "oracle plan wrappers --born YEAR", "needed": "oracle plan wrappers --needed-before GBP",
            "monthly": "oracle plan set --monthly GBP"}


def _on_off(text: str) -> bool:
    if text.lower() not in ("on", "off"):
        raise argparse.ArgumentTypeError("on or off")
    return text.lower() == "on"


SETTERS = (("lisa", _on_off, "on or off: show the Lifetime ISA beside the others (off by default)"),
           ("salary", float, "GBP a year before tax"),
           ("employee_pct", float, "your workplace pension contribution, %% (relief included)"),
           ("employer_pct", float, "your employer's contribution, %%"),
           ("on_salary", _on_off, "on: the %% is of the whole salary; off: of qualifying earnings"),
           ("sacrifice", _on_off, "on: your contribution is paid by salary sacrifice"),
           ("born", int, "your year of birth"),
           ("needed_before", float, "GBP a month of your monthly amount you may need before the pension age"),
           ("retire_tax", float, "%% income tax on the taxed part of the pension when drawn"),
           ("pension_fee", float, "the pension's yearly charge, %% of the pot"),
           ("pension_charge", float, "%% taken from each pension contribution"))


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("wrappers", description="where the next pound goes: workplace pension (tax relief, employer "
                                               "money, salary sacrifice), Stocks and Shares ISA and, when on, the "
                                               "Lifetime ISA, compared per GBP 100 a month and put in order; flags "
                                               "save your numbers to [wrappers] first")
    for key, kind, text in SETTERS:
        p.add_argument(f"--{key.replace('_', '-')}", dest=key, type=kind, help=text)
    p.add_argument("--growth", type=float, default=ASSUMED_GROWTH * 100,
                   help="assumed yearly growth in %% after fund costs (default %(default)s)")
    p.set_defaults(handler=run_wrappers)


def run_wrappers(ctx: AppContext, args: argparse.Namespace) -> int:
    values = {key: getattr(args, key) for key, _, _ in SETTERS if getattr(args, key) is not None}
    check_growth(args.growth / 100)
    if values:
        save_wrappers(ctx, values)
        print(f"Saved [wrappers] {', '.join(values)}.")
    print_comparison(wrapper_comparison(ctx, args.growth / 100))
    return 0


def _wrap(text: str, first: str = "  ", rest: str = "  ") -> None:
    print(textwrap.fill(text, 100, initial_indent=first, subsequent_indent=rest))


def print_comparison(c: WrapperComparison) -> None:
    print(f"Where the next pound goes (rates {c.tax_year}, checked {c.checked}). "
          f"Lifetime ISA: {'on' if c.lisa else 'off'}")
    if c.age is not None:
        print(f"Age {c.age}, pension from {c.access_age} ({c.years} years). Monthly amount "
              f"{'£' + format(c.monthly, ',.0f') if c.monthly else 'not set'}.")
    if c.steps:
        print("Order")
        for i, s in enumerate(c.steps, 1):
            _wrap(f"{i}. {s.text}", "  ", "     ")
    if c.rows:
        at_60 = c.rows[0].at_60 is not None
        print(f"Per £{PER:,.0f} a month of take-home pay for {c.paid_years} years at {c.growth:.1%} a year (assumed), "
              f"valued at {c.access_age}")
        head = f"  {'wrapper':<22} {'paid in':>9} {'added':>8} {'fees':>7} {'pot':>9} {'spendable':>10}"
        print(head + (f" {'at 60':>9}" if at_60 else "") + f" {'per £1':>7}  access")
        for r in c.rows:
            line = (f"  {r.label:<22} {r.paid_in:>9,.0f} {r.added:>8,.0f} {r.fees:>7,.0f} {r.pot:>9,.0f} "
                    f"{r.spendable:>10,.0f}")
            print(line + (f" {r.at_60:>9,.0f}" if at_60 else "") + f" {r.per_pound:>7.2f}  {r.access}")
        print(f"  spendable: taken at {c.access_age}" + (", at 60: nothing more paid in after" if at_60 else "")
              + "; per £1: spendable" + (" at 60" if at_60 else "") + " per £1 of take-home paid in")
    if c.isa_yearly:
        print(f"ISA use: £{c.isa_yearly:,.0f} a year of the £{c.isa_allowance:,.0f} allowance.")
    for note in c.notes:
        _wrap(note, "- ", "  ")
    if c.missing:
        print("Missing:")
        for m in c.missing:
            _wrap(m.text, "- ", "  ")
            print(f"  CLI: {COMMANDS[m.step]}")
    print("Sources (gov.uk unless named; re-check each April):")
    for label, url in c.sources:
        print(f"  {label}: {url}")
