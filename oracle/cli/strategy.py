"""`oracle lab costs|strategies|strategy|trend-check`: the strategy test type (research-lab 02)."""

from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.strategy_service import (
    STRATEGIES, CostView, StrategyTest, StrategyWindow, cost_view, price_trade, run_and_keep_strategy, strategy_tests,
    trend_check,
)


def add_commands(sub) -> None:
    costs = sub.add_parser("costs", description="the UK retail cost model a strategy test pays (from [plan]); with "
                                                "--value, what one trade costs")
    costs.add_argument("--value", type=float, help="a trade's value in GBP")
    costs.add_argument("--sell", action="store_true", help="price a sell (default a buy)")
    costs.add_argument("--currency", default="GBP", help="the listing's currency (FX on anything but GBP)")
    costs.add_argument("--uk-shares", action="store_true", help="a UK company's shares (stamp duty on buys)")
    costs.add_argument("--band", help="the size band for the spread, e.g. 'Small Cap'")
    costs.set_defaults(handler=run_costs)
    sub.add_parser("strategies", description="strategy tests with code and their newest kept results").set_defaults(
        handler=run_strategies)
    strat = sub.add_parser("strategy", description="run a registered strategy test against index buy-and-hold after "
                                                   "UK retail costs, train and confirm, and keep it")
    strat.add_argument("key")
    strat.add_argument("--show", action="store_true", help="print the newest kept result instead of running")
    strat.set_defaults(handler=run_strategy_cmd)
    sub.add_parser("trend-check", description="reproduce the kept trend overlay result through the strategy engine "
                                              "(a check; never judged again)").set_defaults(handler=run_trend_check)


def print_costs(v: CostView) -> None:
    print(f"Platform: {v.platform} ({v.percent_fee:.2f}% a year, cap £{v.percent_cap:.2f}; flat £{v.flat_fee:.2f} a "
          f"month); dealing £{v.dealing_fee:.2f} a trade; FX {v.fx_fee:.2f}% a trade on non-GBP listings; stamp duty "
          f"{v.stamp_duty:.1%} on UK share buys; pot £{v.pot:,.0f}")
    print("Half spread a trade: " + ", ".join(f"{b} {x:.2%}" for b, x in v.half_spreads)
          + f"; no band {v.default_half_spread:.2%}")
    for n in v.notes:
        print(f"  {n}")


def run_costs(ctx: AppContext, args: argparse.Namespace) -> int:
    print_costs(cost_view(ctx))
    if args.value is not None:
        c = price_trade(ctx, args.value, "sell" if args.sell else "buy", args.currency, args.uk_shares, args.band)
        print(f"A {'sell' if args.sell else 'buy'} of £{args.value:,.2f}: dealing £{c.dealing:.2f}, spread "
              f"£{c.spread:.2f}, stamp duty £{c.stamp:.2f}, FX £{c.fx:.2f}; total £{c.total:.2f}")
    return 0


def run_strategies(ctx: AppContext, args: argparse.Namespace) -> int:
    kept = strategy_tests(ctx)
    if not STRATEGIES and not kept:
        print("No strategy test has code yet: register one, commit it, then add its rule.")
    for key, s in STRATEGIES.items():
        t = kept.get(key)
        print(f"{key}: {s.label}; {'not run' if t is None else f'{t.verdict} ({t.run_at[:16]})'}")
    for key in sorted(set(kept) - set(STRATEGIES)):
        print(f"{key}: kept {kept[key].verdict} ({kept[key].run_at[:16]}); its code is gone")
    return 0


def _window(w: StrategyWindow) -> None:
    print(f"{w.window} {w.start} to {w.end}" + (" (reported only)" if w.verdict is None else ""))
    print(f"  {'line':<36}{'return':>9}{'CAGR':>9}{'fall':>9}{'Sharpe':>8}{'trades':>8}{'turnover':>10}{'costs £':>10}")
    for x in (w.strategy, w.gross, w.index, w.index_gross):
        paid = x.dealing + x.spread + x.stamp + x.fx + x.platform
        sharpe = "-" if x.sharpe is None else f"{x.sharpe:.2f}"
        print(f"  {x.label[:35]:<36}{x.ret:>+9.1%}{x.cagr:>+9.2%}{x.max_fall:>9.1%}{sharpe:>8}{x.trades:>8}"
              f"{x.turnover:>10.1%}{paid:>10,.0f}")
    s = w.strategy
    print(f"  costs: dealing £{s.dealing:,.0f}, spread £{s.spread:,.0f}, stamp duty £{s.stamp:,.0f}, FX £{s.fx:,.0f}, "
          f"platform £{s.platform:,.0f}")
    dsr = "-" if w.dsr is None else f"{w.dsr:.3f}"
    print(f"  excess CAGR after costs {w.excess:+.2%} (before {w.gross_excess:+.2%}); deflated Sharpe {dsr}"
          + (f"; {w.verdict}" if w.verdict else ""))


def print_strategy(t: StrategyTest) -> None:
    print(f"{t.key}: {t.label}")
    print(f"Rule: {t.rule}")
    print(f"PASS: {t.bar_rule}")
    print(f"Prices {t.first_day} to {t.last_day}; pot £{t.pot:,.0f}")
    _window(t.train)
    _window(t.confirm)
    for n in t.notes:
        print(f"  {n}")
    print(f"Verdict (confirm): {t.verdict}")


def run_strategy_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    t = strategy_tests(ctx).get(args.key) if args.show else run_and_keep_strategy(ctx, args.key)
    if t is None:
        print(f"No {args.key} strategy test kept yet: run it first.")
        return 1
    print_strategy(t)
    return 0


def run_trend_check(ctx: AppContext, args: argparse.Namespace) -> int:
    c = trend_check(ctx)
    print(f"Trend overlay kept {c.run_at[:16]} (to {c.last_day}), verdict {c.recorded_verdict} as recorded")
    print(f"{'window':<9}{'measure':<12}{'recorded':>12}{'reproduced':>12}  match")
    for r in c.rows:
        fmt = (lambda x: f"{x:.0f}") if r.measure == "switches" else (lambda x: f"{x:+.4%}")
        print(f"{r.window:<9}{r.measure:<12}{fmt(r.recorded):>12}{fmt(r.reproduced):>12}  {'yes' if r.matches else 'NO'}")
    print(("Reproduced within rounding. " if c.matches else "NOT reproduced within rounding. ") + c.note)
    return 0 if c.matches else 1
