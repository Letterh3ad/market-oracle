from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.services.context import AppContext
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
    print("Crashes (confirm; each span from the close before it)")
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
