from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.fx_service import credits, fx_history, update_fx


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="fx_command", required=True)
    update = sub.add_parser("update", help="fetch new rates for every currency stored prices use")
    update.add_argument(
        "--source", action="append", dest="sources", metavar="NAME",
        help="use only these sources (repeatable); default: [fx] sources",
    )
    update.set_defaults(handler=update_cmd)
    show = sub.add_parser("show", help="stored rates for a currency")
    show.add_argument("currency")
    show.add_argument("--days", type=int, default=10)
    show.set_defaults(handler=show_cmd)


def update_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    results = asyncio.run(update_fx(ctx, args.sources))
    if not results:
        print("no foreign-currency prices stored yet")
    for r in results:
        if r.source is None:
            print(f"{r.currency}: no rates ({r.error})")
        else:
            print(f"{r.currency}: {r.added} new rates from {r.source}")
    return 1 if any(r.source is None for r in results) else 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    rates = fx_history(ctx, args.currency, args.days)
    if not rates:
        print(f"no {args.currency.upper()} rates stored yet: run `oracle fx update`")
        return 0
    print(f"{'date':<12}{args.currency.upper() + ' per ' + ctx.config.base_currency:>14}  source")
    for r in rates:
        print(f"{r.date.isoformat():<12}{r.units_per_base:>14.4f}  {r.source}")
    for credit in credits(r.source for r in rates):
        print(credit)
    return 0
