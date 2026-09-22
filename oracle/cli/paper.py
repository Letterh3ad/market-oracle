from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.paper_service import ACCOUNTS, FillOutcome, account_view, fill_pending, place_buy


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="paper_command", required=True)
    buy = sub.add_parser("buy", help="queue a manual buy; it fills at the next open")
    buy.add_argument("symbol")
    buy.add_argument("amount", type=float, help="how much to spend, in the base currency")
    buy.set_defaults(handler=buy_cmd)
    sub.add_parser("fill", help="fill orders whose next bar is stored").set_defaults(handler=fill_cmd)
    show = sub.add_parser("show", help="cash, positions and pending orders")
    show.add_argument("--account", choices=ACCOUNTS)
    show.set_defaults(handler=show_cmd)


def buy_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    order = place_buy(ctx, args.symbol, args.amount)
    print(f"{order.symbol}: buy for {args.amount:,.2f} {ctx.config.base_currency} queued; fills at the next open")
    return 0


def fill_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    print_fills(fill_pending(ctx))
    return 0


def print_fills(outcomes: list[FillOutcome]) -> None:
    for o in outcomes:
        what = {"filled": "bought" if o.order.side == "buy" else "sold", "rejected": "REJECTED",
                "pending": "waiting"}[o.status]
        print(f"{o.order.symbol}: {what}" + (f" ({o.note})" if o.note else ""))


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    base = ctx.config.base_currency
    for name in [args.account] if args.account else ACCOUNTS:
        view = account_view(ctx, name)
        print(f"{name}: cash {view.cash:,.2f} {base}")
        for p in view.positions:
            value = f"{p.value:,.2f}" if p.value is not None else "n/a"
            print(f"  {p.symbol:<12}{p.shares:>8} shares  cost {p.cost:>12,.2f}  value {value:>12}")
        for o in view.pending:
            note = f" ({o.note})" if o.note else ""
            print(f"  pending {o.side} {o.symbol} for {o.cash:,.2f} {base}, placed {o.placed_on.isoformat()}{note}")
    return 0
