from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.cli import plan_money
from oracle.services.context import AppContext
from oracle.services.fund_compare_service import FundComparison, fund_comparison, run_and_keep_funds
from oracle.services.trend_service import TrendTest, TrendWindow, run_and_keep_trend, trend_tests


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "the boring plan: index buy and hold, and the tests around it"
    sub = parser.add_subparsers(dest="plan_command", required=True)
    trend = sub.add_parser("trend", description="the pre-registered trend overlay test: the 10-month moving average "
                                                "rule on ^SP500TR, chosen on 1988-2007 (reported only) and judged on "
                                                "2008 to today against buy and hold; fetches the newest days into "
                                                "the research database and keeps the result there")
    trend.add_argument("--show", action="store_true", help="print the newest kept result instead of running")
    trend.set_defaults(handler=run_trend)
    funds = sub.add_parser("funds", description="all-world, developed world, S&P 500 and a UK home-bias mix as "
                                                "sterling accumulating funds after their TER, from long-history "
                                                "proxies, with each real UCITS fund tracked against its proxy; "
                                                "fetches the newest days into the research database and keeps "
                                                "the result there")
    funds.add_argument("--show", action="store_true", help="print the newest kept result instead of running")
    funds.set_defaults(handler=run_funds)
    plan_money.register(sub)


def run_trend(ctx: AppContext, args: argparse.Namespace) -> int:
    t = trend_tests(ctx) if args.show else asyncio.run(run_and_keep_trend(ctx))
    if t is None:
        print("No trend overlay test kept yet: run it first.")
        return 0
    print_trend(t)
    return 0


def print_trend(t: TrendTest) -> None:
    print(f"Trend overlay test on {t.series} ({t.label}), stored {t.first_day} to {t.last_day}, run {t.run_at[:16]} UTC")
    print(textwrap.fill(f"Rule: {t.rule}", 100))
    print(f"Cash earns {t.cash}. Switching cost {t.cost:.1%} of the value per switch.")
    print(f"{'window':<8} {'period':<22} {'line':<14} {'return':>9} {'CAGR':>7} {'worst fall':>10} "
          f"{'switches':>8} {'in cash':>8}")
    for w in (t.choose, t.confirm, *t.beside):
        _window(w)
    print("Crashes (confirm; each span from the close before it, or the window's first close)")
    print(f"{'span':<8} {'period':<22} {'overlay':>9} {'b&h':>9} {'overlay fall':>12} {'b&h fall':>9} {'in cash':>8}")
    for c in t.confirm.crashes:
        print(f"{c.name:<8} {c.start} {c.end} {c.trend_return:>+9.1%} {c.held_return:>+9.1%} {c.trend_fall:>12.1%} "
              f"{c.held_fall:>9.1%} {c.cash_share:>8.0%}")
    for note in t.notes:
        print(textwrap.fill(note, 100, initial_indent="- ", subsequent_indent="  "))
    print(f"Verdict (confirm): {t.verdict}")


def _window(w: TrendWindow) -> None:
    for k, (line, switches) in enumerate(((w.trend, w.switches), (w.held, 0))):
        head = f"{w.window:<8} {w.start} {w.end}" if k == 0 else " " * 30
        name = "Trend overlay" if k == 0 else "Buy and hold"
        print(f"{head} {name:<14} {line.ret:>+9.1%} {line.cagr:>+7.2%} {line.max_fall:>10.1%} {switches:>8} "
              f"{line.cash_share:>8.0%}")
    judged = w.verdict if w.verdict else "reported only"
    print(f"{'':<8} worst fall cut {w.fall_cut:.1%} (PASS needs 33.3%), yearly return given up "
          f"{w.given_up * 100:+.2f} points (PASS needs at most 1.00): ({judged})")


def run_funds(ctx: AppContext, args: argparse.Namespace) -> int:
    c = fund_comparison(ctx) if args.show else asyncio.run(run_and_keep_funds(ctx))
    if c is None:
        print("No fund comparison kept yet: run it first.")
        return 0
    print_funds(c)
    return 0


def print_funds(c: FundComparison) -> None:
    print(f"Fund comparison in {c.base}, accumulating, after each fund's TER; every line is a proxy. Shared history "
          f"{c.start} to {c.end}, run {c.run_at[:16]} UTC")
    print(f"{'fund':<42} {'return':>8} {'yearly':>7} {'worst fall':>10} {'TER':>6} {'TER drag':>8} {'US share':>8}"
          f"   {'own history':<22} {'yearly':>7} {'worst fall':>10}")
    for r in c.rows:
        print(f"{r.label:<42} {r.ret:>+8.1%} {r.cagr:>+7.2%} {r.max_fall:>10.1%} {r.ter:>6.2%} "
              f"{r.ter_drag * 100:>8.2f} {r.us_share:>8.0%}   {f'since {r.own_start}':<22} {r.own_cagr:>+7.2%} "
              f"{r.own_fall:>10.1%}")
        print(f"  {r.source}, {r.kind}")
    print("TER drag: points of return the fund's charge cost over the shared history.")
    print("Tracking: each real fund against its proxy modelled as that fund, from 90 days after the real fund's "
          "first stored day (launch prints are thin)")
    print(f"{'real fund':<52} {'proxy':<9} {'period':<24} {'real':>8} {'proxy':>8} {'diff':>7} {'per year':>8}")
    for t in c.tracking:
        if t.start is None:
            print(f"{t.real_label + ' ' + t.real:<52} {t.proxy:<9} no stored days inside the proxy's")
            continue
        print(f"{t.real_label + ' ' + t.real:<52} {t.proxy:<9} {f'{t.start} {t.end}':<24} {t.real_return:>+8.1%} "
              f"{t.model_return:>+8.1%} {t.difference * 100:>+7.2f} {t.yearly_difference * 100:>+8.2f}")
    if c.fx is not None:
        f = c.fx
        print(f"FX check: {f.symbol} closed {f.close:.2f} {f.currency} on {f.day}, at {f.rate:.4f} {f.currency} per "
              f"{c.base} = {f.close_base:.2f} {c.base}")
    for note in c.notes:
        print(textwrap.fill(note, 100, initial_indent="- ", subsequent_indent="  "))
