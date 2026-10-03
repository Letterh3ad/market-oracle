"""`oracle plan fees` and `oracle plan contributions`: the fee calculator and the contribution tests."""

from __future__ import annotations

import argparse
import textwrap

from oracle.services.context import AppContext
from oracle.services.contributions_service import FUND, SECOND, ContributionTests, contribution_tests
from oracle.services.fees_service import ASSUMED_GROWTH, FeeDrag, fee_drag
from oracle.services.guardrails_service import settle


def register(sub: argparse._SubParsersAction) -> None:
    fees = sub.add_parser("fees", description="what your platform's fees take over a horizon, on the percentage "
                                              "and the flat platform set in oracle.toml [plan], and the pot size "
                                              "where flat beats percentage")
    fees.add_argument("--monthly", type=float, required=True, help="GBP paid in each month")
    fees.add_argument("--years", type=int, default=25)
    fees.add_argument("--growth", type=float, default=ASSUMED_GROWTH * 100,
                      help="assumed yearly growth in %% after fund costs (default %(default)s)")
    fees.add_argument("--start", type=float, default=0.0, help="GBP already in the pot")
    fees.set_defaults(handler=run_fees)
    c = sub.add_parser("contributions", description="lump sum against a 12-month drip from every start month, "
                                                    "monthly contributions over every window, rebalancing a two-fund "
                                                    "mix and cash drag, on fund comparison's GBP after-TER series "
                                                    "(proxies; run `oracle plan funds` first), after your platform's "
                                                    "fees")
    c.add_argument("--monthly", type=float, required=True, help="GBP paid in each month")
    c.add_argument("--lump", type=float, default=None, help="GBP for lump against drip (default 12 months' worth)")
    c.add_argument("--years", type=int, default=10, help="length of each monthly-contribution window")
    c.add_argument("--fund", default=FUND, help="the fund tested (a fund comparison key: all_world, developed, "
                                                 "sp500, uk, uk_mix)")
    c.add_argument("--second", default=SECOND, help="the mix's second fund (a fund comparison key), or 'none'")
    c.add_argument("--weight", type=float, default=75.0, help="%% of the mix in the first fund")
    c.add_argument("--band", type=float, default=5.0, help="rebalancing band in points")
    c.add_argument("--cash", type=float, default=5.0, help="%% of the pot left in cash, for cash drag")
    c.add_argument("--cash-rate", type=float, default=0.0, help="%% a year that cash earns")
    c.set_defaults(handler=run_contributions)


def run_fees(ctx: AppContext, args: argparse.Namespace) -> int:
    settle(ctx)
    print_fees(fee_drag(ctx, args.monthly, args.years, args.growth / 100, args.start))
    return 0


def print_fees(d: FeeDrag) -> None:
    print(f"£{d.monthly:,.0f} a month for {d.years} years at {d.growth:.1%} a year (assumed), from £{d.start:,.0f}: "
          f"£{d.contributed:,.0f} paid in. You use the {d.using} platform.")
    print(f"{'platform':<44} {'pot':>12} {'fees paid':>11} {'fee drag':>11} {'drag %':>7}")
    plain = d.lines[0].value
    for line in d.lines:
        print(f"{line.label:<44} {line.value:>12,.0f} {line.fees:>11,.0f} {line.drag:>11,.0f} "
              f"{line.drag / plain if plain else 0:>7.1%}")
    if d.crossover is None:
        print("Flat never beats percentage at these fees.")
    else:
        when = f" (the percentage pot passes it in year {d.crossover_year})" if d.crossover_year else ""
        print(f"Flat beats percentage once the pot is above £{d.crossover:,.0f}{when}.")
    for note in d.notes:
        print(textwrap.fill(note, 100, initial_indent="- ", subsequent_indent="  "))


def run_contributions(ctx: AppContext, args: argparse.Namespace) -> int:
    settle(ctx)
    second = None if args.second.lower() == "none" else args.second
    print_contributions(contribution_tests(ctx, args.monthly, args.lump, args.years, growth=args.fund, second=second,
                                           weight=args.weight / 100, band=args.band / 100,
                                           cash_share=args.cash / 100, cash_rate=args.cash_rate / 100))
    return 0


def _spread(name: str, s) -> str:
    return (f"{name:<28} {s.count:>6} {s.wins:>7.0%} {s.median:>+8.1%} {s.p10:>+8.1%} {s.p90:>+8.1%} "
            f"{s.worst:>+8.1%} {s.best:>+8.1%}")


def print_contributions(t: ContributionTests) -> None:
    s = t.series
    print(f"On {s.label} ({s.source}; GBP, accumulating, after its {s.ter:.2%} TER, {s.kind}); fees: {t.fees}.")
    print(f"Whole months {t.first} to {t.last} ({t.months} months, all fund comparison has stored).")
    if t.starts:
        print(f"Lump vs drip: {len(t.starts)} start months, {t.starts[0].start} to {t.starts[-1].start}.")
    if t.window_span:
        print(f"Monthly: {t.window_count} windows of {t.years} years, starting {t.window_span[0]} to {t.window_span[1]}.")
    print(f"{'':<28} {'count':>6} {'above 0':>7} {'median':>8} {'p10':>8} {'p90':>8} {'worst':>8} {'best':>8}")
    print(_spread(f"Lump £{t.amount:,.0f} vs 12-mo drip", t.lump))
    print(_spread(f"£{t.monthly:,.0f}/mo, {t.years}-y gain", t.windows))
    print("  (first row: how far the lump ends ahead of the drip at the drip's end; second: the pot over the money "
          "paid in)")
    if t.mixes:
        a, b = t.mix_span
        print(f"Rebalancing {t.weight:.0%} {s.label} / {1 - t.weight:.0%} {t.second.label} ({t.second.source}), "
              f"£{t.monthly:,.0f} a month, {a} to {b}:")
        print(f"{'rule':<16} {'pot':>12} {'paid in':>10} {'growth':>8} {'worst fall':>10} {'fees':>8} {'trades':>6} "
              f"{'end weight':>10}")
        for m in t.mixes:
            r = m.result
            print(f"{m.rule:<16} {r.value:>12,.0f} {r.contributed:>10,.0f} {r.growth - 1:>+8.1%} {r.worst_fall:>10.1%} "
                  f"{r.fees:>8,.0f} {r.trades:>6} {r.weight:>10.1%}")
    c = t.cash
    print(f"Cash drag: {c.share:.0%} of the pot in cash earning {c.cash_rate:.1%} gives up {c.given_up * 100:.2f} "
          f"points a year (growth of £1: {c.held:.2f} against {c.invested:.2f} fully invested).")
    for note in t.notes:
        print(textwrap.fill(note, 100, initial_indent="- ", subsequent_indent="  "))
