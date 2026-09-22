from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.prices_service import (
    price_history,
    price_history_in_base,
    price_sources,
    update_listing,
    update_watched,
)


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="prices_command", required=True)
    update = sub.add_parser("update", help="fetch new daily bars: one listing, or every watched one plus FX")
    update.add_argument("symbol", nargs="?", help="omit to update every watchlist")
    update.set_defaults(handler=update_cmd)
    show = sub.add_parser("show", help="stored daily bars for a listing")
    show.add_argument("symbol")
    show.add_argument("--days", type=int, default=10, help="most recent trading days (default 10)")
    show.add_argument("--base", action="store_true", help="add the close in base currency (GBP by default)")
    show.set_defaults(handler=show_cmd)
    sub.add_parser("sources", help="where prices come from, in order").set_defaults(handler=sources_cmd)


def update_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.symbol:
        r = asyncio.run(update_listing(ctx, args.symbol))
        print(f"{r.symbol}: {r.added} new bars, last {r.last_date} (from {r.source})")
        return 0
    run = asyncio.run(update_watched(ctx))
    if not run.listings:
        print("nothing watched: add listings with `oracle watch add SYMBOL`")
        return 0
    for o in run.listings:
        print(f"{o.symbol}: FAILED ({o.error})" if o.error else f"{o.symbol}: {o.added} new bars (from {o.source})")
    for f in run.fx:
        print(f"{f.currency}: no rates ({f.error})" if f.source is None else f"{f.currency}: {f.added} new rates from {f.source}")
    return 1 if run.failed or any(f.source is None for f in run.fx) else 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = price_history_in_base(ctx, args.symbol, args.days) if args.base else None
    bars = [r.bar for r in rows] if rows is not None else price_history(ctx, args.symbol, args.days)
    if not bars:
        print(f"no prices stored yet: run `oracle prices update {args.symbol.upper()}`")
        return 0
    base = f"{'in ' + ctx.config.base_currency:>12}" if rows is not None else ""
    print(f"{'date':<12}{'close':>12}{'adj close':>12}{'volume':>14}  {bars[-1].currency}{base}")
    for i, b in enumerate(bars):
        adj = f"{b.adj_close:.2f}" if b.adj_close is not None else "n/a"
        volume = f"{b.volume:,}" if b.volume is not None else "n/a"
        converted = ""
        if rows is not None:
            value = rows[i].close_base
            converted = f"{f'{value:.2f}' if value is not None else 'n/a':>15}"
        print(f"{b.date.isoformat():<12}{b.close:>12.2f}{adj:>12}{volume:>14}{converted}")
    if rows is not None and any(r.close_base is None for r in rows):
        print("n/a: no exchange rate stored for that day; run `oracle fx update`")
    return 0


def sources_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    print(f"{'#':<4}{'source':<10}state")
    for s in price_sources(ctx):
        state = "on" if s.enabled else f"off ({s.reason})"
        print(f"{s.position or '-':<4}{s.name:<10}{state}")
    return 0
