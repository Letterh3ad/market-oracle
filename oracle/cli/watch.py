from __future__ import annotations

import argparse

from oracle.cli.common import cut
from oracle.services.context import AppContext
from oracle.services.fx_service import credits
from oracle.services.watch_service import DEFAULT_LIST, show_watchlist, unwatch, watch, watchlists


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="watch_command", required=True)
    for verb, handler, summary in (
        ("add", add_cmd, "watch one listing (the line that gets priced)"),
        ("remove", remove_cmd, "stop watching a listing"),
    ):
        p = sub.add_parser(verb, help=summary)
        p.add_argument("symbol")
        p.add_argument("--list", default=DEFAULT_LIST, help=f"watchlist name (default {DEFAULT_LIST})")
        p.set_defaults(handler=handler)
    show = sub.add_parser("show", help="a watchlist's listings")
    show.add_argument("list", nargs="?", default=DEFAULT_LIST)
    show.set_defaults(handler=show_cmd)
    sub.add_parser("lists", help="every watchlist").set_defaults(handler=lists_cmd)


def add_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    w = watch(ctx, args.symbol, args.list)
    state = "watching" if w.added else "already watching"
    print(f"{state} {w.name} ({w.symbol}) on {w.watchlist}")
    return 0


def remove_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if unwatch(ctx, args.symbol, args.list):
        print(f"stopped watching {args.symbol} on {args.list}")
        return 0
    print(f"{args.symbol} is not on {args.list}")
    return 1


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    items = show_watchlist(ctx, args.list)
    if not items:
        print(f"{args.list} is empty: add with `oracle watch add SYMBOL`")
        return 0
    base = ctx.config.base_currency
    print(f"{'symbol':<12}{'name':<26}{'exch':<6}{'country':<15}{'last close':>16}{'in ' + base:>12}{'day':>9}  date")
    for i in items:
        if i.missing:
            print(f"{i.symbol:<12}MISSING: no longer in the universe (remove, or refresh again later)")
            continue
        close = f"{i.last_close:.2f} {i.price_currency}" if i.last_close is not None else "not priced"
        in_base = f"{i.close_base:.2f}" if i.close_base is not None else "n/a"
        day = f"{i.day_change * 100:+.2f}%" if i.day_change is not None else ""
        notes = "  ".join(n for n in (
            i.last_date.isoformat() if i.last_date else "",
            "STALE" if i.stale else "",
            "delisted" if i.delisted else "",
        ) if n)
        print(
            f"{i.symbol:<12}{cut(i.name, 25):<26}{i.exchange or '':<6}{cut(i.country, 14):<15}"
            f"{close:>16}{in_base:>12}{day:>9}  {notes}".rstrip()
        )
    for credit in credits(i.rate_source for i in items):
        print(credit)
    return 0


def lists_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = watchlists(ctx)
    if not rows:
        print("no watchlists yet: `oracle watch add SYMBOL` starts main")
        return 0
    for w in rows:
        missing = f"  ({w.missing} missing)" if w.missing else ""
        print(f"{w.name:<24}{w.items:>4} listings{missing}")
    return 0
