from __future__ import annotations

import argparse

from oracle.cli.common import print_fills, print_lines
from oracle.services.context import AppContext
from oracle.services.paper_service import (
    ACCOUNTS, AI_OFF, CORE_SAT, INDEX, LAYA, LINE_NAMES, REGIME_LINES, STRATEGY, TRADERS, UNVALIDATED, Order, Side, account_score, account_view, ai_step, comparison,
    dividend_credits,
    decision_log, equity_history, fill_pending, frozen_note, place_buy, place_sell, strategy_step,
)
from oracle.services.regime_service import laya_card, off_note
from oracle.services.tick_service import run_index_step, run_line_step, tilt_line

AI_HEADER = f"AI decisions ({UNVALIDATED})"


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
    step = sub.add_parser("step", help="run an account's rules and queue its orders")
    step.add_argument("--account", choices=TRADERS, default="strategy",
                      help="index buys ACWI with all its cash once and holds it, ai trades the research "
                           "verdicts, core-sat buys its equal-weight core once (never rebalanced; its satellite at "
                           "weight 0 only sells), laya trades Laya's daily news score alone; strategy, regime, "
                           "regime-ai and regime-v2 are frozen and step nothing")
    step.set_defaults(handler=step_cmd)
    log = sub.add_parser("log", help="an account's recent decisions")
    log.add_argument("--account", choices=TRADERS, default="strategy")
    log.set_defaults(handler=log_cmd)
    history = sub.add_parser("history", help="every account against buy-and-hold since the AI account started, "
                                                "or one account's daily equity")
    history.add_argument("--account", choices=ACCOUNTS)
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
    if order.side == Side.BUY:
        return f"{order.cash:,.2f} {base}"
    return "the whole position" if order.shares is None else f"{order.shares} shares"


def fill_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    print_fills(fill_pending(ctx))
    return 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    base = ctx.config.base_currency
    for name in [args.account] if args.account else ACCOUNTS:
        view, score = account_view(ctx, name), account_score(ctx, name)
        print(f"{name}: equity {score.equity:,.2f} {base} ({score.return_:+.2%} since start), cash {view.cash:,.2f}")
        print(f"  realised {score.realised:+,.2f}  unrealised {score.unrealised:+,.2f}  costs paid {score.costs:,.2f}"
              f"  dividends {score.dividends:,.2f}  largest fall {score.max_fall:.1%}")
        for p in view.positions:
            value = f"{p.value:,.2f}" if p.value is not None else "n/a"
            print(f"  {p.symbol:<12}{p.shares:>8} shares  cost {p.cost:>12,.2f}  value {value:>12}")
        for o in view.pending:
            note = f" ({o.note})" if o.note else ""
            print(f"  pending {o.side} {o.symbol}, {_size(o, base)}, placed {o.placed_on.isoformat()}{note}")
        for c in dividend_credits(ctx, name):
            when = c.pay_date.isoformat() + ("" if c.pay_known else " (assumed)")
            print(f"  dividend {c.symbol} paid {when}, ex {c.ex_date.isoformat()}: {c.shares} x {c.amount:g} {c.currency}"
                  f" = {c.cash:,.2f} {base}")
    return 0


def history_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.account is None:
        return _compare(ctx)
    rows = equity_history(ctx, args.account)
    if not rows:
        print(f"no equity recorded for {args.account} yet: it is recorded each time orders are filled")
        return 0
    for day, equity in rows:
        print(f"{day.isoformat()}  {equity:>12,.2f} {ctx.config.base_currency}")
    return 0


def _compare(ctx: AppContext) -> int:
    c = comparison(ctx)
    for note in c.notes:
        print(note)
    for line in c.lines:
        print(f"  {line.caption}")
    print()
    print_lines(c.lines, ctx.config.base_currency)
    days = sorted({d for line in c.lines for d, _ in line.points})
    if not days:
        return 0
    print()
    print(f"{'day':<12}" + "".join(f"{line.name:>14}" for line in c.lines))
    by_line = [dict(line.points) for line in c.lines]
    for day in days:
        print(f"{day.isoformat():<12}" + "".join(f"{f'{p[day]:+.2%}' if day in p else '':>14}" for p in by_line))
    return 0


def step_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.account in REGIME_LINES:
        return _regime_step(ctx, args.account)
    if args.account == INDEX:
        decisions = run_index_step(ctx)
        if not decisions:
            print("index: nothing to do, it holds ACWI or its buy waits to fill")
        _print_decisions(ctx, decisions)
        return 0
    decisions = strategy_step(ctx) if args.account == STRATEGY else ai_step(ctx)
    if decisions is None:
        print(frozen_note(STRATEGY) if args.account == STRATEGY else AI_OFF)
        return 0
    if args.account == "ai":
        print(AI_HEADER)
    _print_decisions(ctx, decisions)
    return 0


def _regime_step(ctx: AppContext, account: str) -> int:
    run = run_line_step(ctx, account).run
    if run is None:
        print(off_note(account))
        return 0
    if run.mood:
        flags = f" ({', '.join(run.flags)})" if run.flags else ""
        print(f"{LINE_NAMES[account]}: mood {run.mood}{flags} on {run.day.isoformat()}")
    if run.tilt:
        print(f"no tilt, rules weights traded: {run.tilt.error}" if run.tilt.error
              else f"{tilt_line(run.tilt)} ({run.tilt.reason})")
    if run.lag:
        print(f"WARNING: {run.lag}")
    if account in (CORE_SAT, LAYA):
        print(laya_card(ctx)["note"])
    _print_decisions(ctx, run.decisions)
    return 0


def _print_decisions(ctx: AppContext, decisions) -> None:
    if not decisions:
        print("no decisions: nothing watched or held (add listings with `oracle watch add SYMBOL`)")
    for d in decisions:
        amount = f" for {d.cash:,.2f} {ctx.config.base_currency}" if d.cash is not None else ""
        print(f"{d.symbol}: {d.action}{amount} ({d.reason})")


def log_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = decision_log(ctx, args.account)
    if rows and args.account == "ai":
        print(AI_HEADER)
    for d in rows:
        print(f"{d.day.isoformat()}  {d.symbol:<12}{d.action:<6}{d.reason}{_parts(d.explanation)}")
    return 0


def _parts(why: dict | None, top: int = 3) -> str:
    """A regime lean's mood and its largest parts."""
    if not why:
        return ""
    parts = ", ".join(f"{m} {v:+.2f}" for m, v in list(why["parts"].items())[:top])
    return f" [{why['mood']}: {parts}]"
