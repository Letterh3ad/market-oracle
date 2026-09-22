from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.prices_service import price_history, update_listing


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="prices_command", required=True)
    update = sub.add_parser("update", help="fetch new daily bars for a listing")
    update.add_argument("symbol")
    update.set_defaults(handler=update_cmd)
    show = sub.add_parser("show", help="stored daily bars for a listing")
    show.add_argument("symbol")
    show.add_argument("--days", type=int, default=10, help="most recent trading days (default 10)")
    show.set_defaults(handler=show_cmd)


def update_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(update_listing(ctx, args.symbol))
    print(f"{r.symbol}: {r.added} new bars, last {r.last_date} (from {r.source})")
    return 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    bars = price_history(ctx, args.symbol, args.days)
    if not bars:
        print(f"no prices stored yet: run `oracle prices update {args.symbol.upper()}`")
        return 0
    print(f"{'date':<12}{'close':>12}{'adj close':>12}{'volume':>14}  {bars[-1].currency}")
    for b in bars:
        adj = f"{b.adj_close:.2f}" if b.adj_close is not None else "n/a"
        volume = f"{b.volume:,}" if b.volume is not None else "n/a"
        print(f"{b.date.isoformat():<12}{b.close:>12.2f}{adj:>12}{volume:>14}")
    return 0
