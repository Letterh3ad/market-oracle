from __future__ import annotations

import argparse
import textwrap

from oracle.cli.common import iso_day, print_lines
from oracle.services.backtest_service import run_backtest
from oracle.services.context import AppContext
from oracle.services.watch_service import DEFAULT_LIST


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("Replay the strategy's fixed rules over a watchlist's stored prices, against buying and "
                          "holding the same listings, against the momentum baseline (the top listings by "
                          "12-1 momentum, re-picked monthly) and against the regime line (a blend of known methods "
                          "weighted by the market's mood; its caption names the inputs it had). Nothing in the paper "
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
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    r = run_backtest(ctx, args.list, args.start, args.end, args.slippage_bps, args.commission)
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
    if not r.trades:
        print("No trades.")
    for t in r.trades:
        print(f"{t.day.isoformat()}  {t.side:<5}{t.symbol:<12}{t.shares:>7} at {t.price:,.2f} {t.currency}"
              f"  {t.cash:>+12,.2f} {base}  ({t.reason})")
    return 0
