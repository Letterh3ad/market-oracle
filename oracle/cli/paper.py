from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.paper_service import ACCOUNTS, FillOutcome, Order, account_score, account_view, equity_history, fill_pending, place_buy, place_sell


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="paper_command", required=True)
    buy = sub.add_parser("buy", help="queue a manual buy; it fills at the next open")
    buy.add_argument("symbol")
    buy.add_argument("amount", type=float, help="how much to spend, in the base currency")
    buy.set_defaults(handler=buy_cmd)
    sell = sub.add_parser("sell", help="queue a manual sell; it fills at the next open")
    sell.add_argument("symbol")
    size = sell.add_mutually_exclusive_group(required=True)
    size.add_argument("--shares", type=int)
    size.add_argument("--all", action="store_true", help="the whole position")
    sell.set_defaults(handler=sell_cmd)
    sub.add_parser("fill", help="fill orders whose next bar is stored").set_defaults(handler=fill_cmd)
    show = sub.add_parser("show", help="cash, positions and pending orders")
    show.add_argument("--account", choices=ACCOUNTS)
    show.set_defaults(handler=show_cmd)
    history = sub.add_parser("history", help="daily equity of an account")
    history.add_argument("--account", choices=ACCOUNTS, default="strategy")
    history.set_defaults(handler=history_cmd)


def buy_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    order = place_buy(ctx, args.symbol, args.amount)
    print(f"{order.symbol}: buy for {args.amount:,.2f} {ctx.config.base_currency} queued; fills at the next open")
    return 0


def sell_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    order = place_sell(ctx, args.symbol, None if args.all else args.shares)
    print(f"{order.symbol}: sell of {_size(order, ctx.config.base_currency)} queued; fills at the next open")
    return 0


def _size(order: Order, base: str) -> str:
    if order.side == "buy":
        return f"{order.cash:,.2f} {base}"
    return "the whole position" if order.shares is None else f"{order.shares} shares"


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
        view, score = account_view(ctx, name), account_score(ctx, name)
        print(f"{name}: equity {score.equity:,.2f} {base} ({score.return_:+.2%} since start), cash {view.cash:,.2f}")
        print(f"  realised {score.realised:+,.2f}  unrealised {score.unrealised:+,.2f}  costs paid {score.costs:,.2f}"
              f"  largest fall {score.max_fall:.1%}")
        for p in view.positions:
            value = f"{p.value:,.2f}" if p.value is not None else "n/a"
            print(f"  {p.symbol:<12}{p.shares:>8} shares  cost {p.cost:>12,.2f}  value {value:>12}")
        for o in view.pending:
            note = f" ({o.note})" if o.note else ""
            print(f"  pending {o.side} {o.symbol}, {_size(o, base)}, placed {o.placed_on.isoformat()}{note}")
    return 0


def history_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = equity_history(ctx, args.account)
    if not rows:
        print(f"no equity recorded for {args.account} yet: it is recorded each time orders are filled")
        return 0
    for day, equity in rows:
        print(f"{day.isoformat()}  {equity:>12,.2f} {ctx.config.base_currency}")
    return 0
