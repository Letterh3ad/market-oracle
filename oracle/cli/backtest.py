from __future__ import annotations

import argparse
import sys
import textwrap
import time

from oracle.cli.common import iso_day, print_lines
from oracle.services.backtest_service import Figures, WalkForward, run_backtest
from oracle.services.context import AppContext
from oracle.services.watch_service import DEFAULT_LIST


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("Replay the strategy's fixed rules over a watchlist's stored prices, against holding "
                          "ACWI and SPY (the honest \"do nothing\", shown first), against buying and "
                          "holding the same listings (today's list, so survivorship flatters it), against the momentum baseline (the top listings by "
                          "12-1 momentum, re-picked monthly) and against the regime lines (a blend of known methods "
                          "weighted by the market's mood, and regime-v2: the same without hold padding and with a "
                          "rotation out of stale holdings; and core-sat: an equal-weight buy and hold bought once and "
                          "never rebalanced, its regime satellite at weight 0; each caption names the inputs it had). Nothing in the paper "
                          "accounts changes.")
    parser.add_argument("--list", default=DEFAULT_LIST, help=f"watchlist name (default {DEFAULT_LIST})")
    parser.add_argument("--from", dest="start", type=iso_day, required=True, metavar="YYYY-MM-DD",
                        help="the first day replayed")
    parser.add_argument("--to", dest="end", type=iso_day, required=True, metavar="YYYY-MM-DD",
                        help="the last day replayed")
    parser.add_argument("--slippage-bps", type=float, default=10.0,
                        help="how much worse than the open each fill is, in basis points (default 10)")
    parser.add_argument("--commission", type=float, default=0.0,
                        help="a flat fee per trade in the base currency (default 0)")
    parser.add_argument("--walk-forward", action="store_true",
                        help="also judge every line over back-to-back six-month segments, each a fresh start, "
                             "against buy and hold, and add the regime_wf line (mood weights refitted at each "
                             "segment's start from the two years before)")
    parser.add_argument("--regime-ai", action="store_true",
                        help="also replay the regime-ai line: the model's daily tilt asked for every day (no date in "
                             "the prompt, same guards), one model call per day, so hours on a local model")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    r = run_backtest(ctx, args.list, args.start, args.end, args.slippage_bps, args.commission, args.walk_forward,
                     regime_ai=args.regime_ai, progress=_every(PROGRESS_S) if args.regime_ai else None)
    base = ctx.config.base_currency
    n = len(r.listings)
    print(f"Backtest of the {r.watchlist} list ({n} listing{'' if n == 1 else 's'}), "
          f"{r.start.isoformat()} to {r.end.isoformat()}")
    for note in r.notes:
        print(textwrap.fill(note, 96, initial_indent="- ", subsequent_indent="  "))
    print()
    for line in r.lines:
        print(f"  {line.caption}")
    print()
    print_lines(r.lines, base)
    print()
    if r.walk_forward:
        print_walk_forward(r.walk_forward)
        print()
    if not r.trades:
        print("No trades.")
    for t in r.trades:
        print(f"{t.day.isoformat()}  {t.side:<5}{t.symbol:<12}{t.shares:>7} at {t.price:,.2f} {t.currency}"
              f"  {t.cash:>+12,.2f} {base}  ({t.reason})")
    return 0


PROGRESS_S = 30.0
"""How often a regime-ai replay says where it is: often enough to see it move, not a line a day."""


def _every(seconds: float):
    last = [-seconds]

    def say(text: str) -> None:
        if time.monotonic() - last[0] >= seconds:
            last[0] = time.monotonic()
            print(text, file=sys.stderr, flush=True)
    return say
def print_walk_forward(wf: WalkForward) -> None:
    head = f"{'line':<14}{'return':>9}{'largest fall':>14}{'over b&h':>10}{'Sharpe':>8}{'ret/fall':>10}"
    print("Walk-forward by segment (each a fresh start; over b&h is the return over buy and hold's in the same days)")
    for k, (a, b) in enumerate(wf.segments):
        print(f"{a.isoformat()} to {b.isoformat()}")
        print(head)
        for line in wf.lines:
            print(f"{line.name:<14}{_figures(line.segments[k])}")
    overall_head = (f"{'line':<14}{'return':>9}{'worst segment fall':>20}{'over b&h':>10}{'Sharpe':>8}"
                    f"{'cmp / worst fall':>17}")
    print("Over all segments (returns compounded, Sharpe of every segment's days, the worst segment's fall)")
    print(f"{overall_head}{'beat buy and hold':>19}")
    for line in wf.lines:
        beat = "" if line.beat is None else f"{line.beat} of {len(wf.segments)}"
        print(f"{line.name:<14}{_overall_figures(line.overall)}{beat:>19}")
    print()
    print("Regime-wf weights fitted at each segment's start (mood: method weights)")
    for day, presets in wf.fits:
        print(day.isoformat())
        for mood, weights in presets.items():
            print(f"  {mood:<14}" + "  ".join(f"{m} {w:.2f}" for m, w in weights.items()))


def _figures(f: Figures) -> str:
    over = "" if f.excess is None else f"{f.excess:+.2%}"
    sharpe = "" if f.sharpe is None else f"{f.sharpe:.2f}"
    per_fall = "" if f.per_fall is None else f"{f.per_fall:.2f}"
    return f"{f.ret:>+9.2%}{f.max_fall:>14.1%}{over:>10}{sharpe:>8}{per_fall:>10}"


def _overall_figures(f: Figures) -> str:
    over = "" if f.excess is None else f"{f.excess:+.2%}"
    sharpe = "" if f.sharpe is None else f"{f.sharpe:.2f}"
    per_fall = "" if f.per_fall is None else f"{f.per_fall:.2f}"
    return f"{f.ret:>+9.2%}{f.max_fall:>20.1%}{over:>10}{sharpe:>8}{per_fall:>17}"
