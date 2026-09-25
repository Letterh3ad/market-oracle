from __future__ import annotations

import argparse

from oracle.cli.common import cut, iso_day
from oracle.services.context import AppContext
from oracle.services.factors_service import factor_table


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--list", dest="watchlist", default=None, metavar="NAME",
                        help="show one watchlist's listings (still ranked across the whole set)")
    parser.add_argument("--as-of", type=iso_day, default=None, metavar="YYYY-MM-DD",
                        help="rank on the closes up to this day (default today)")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    t = factor_table(ctx, args.as_of, args.watchlist)
    shown = f"{t.watchlist}'s listings, ranked" if t.watchlist else "Ranked"
    print(f"Price factors as of {t.as_of.isoformat()}: {shown} across {t.set_size} watched, held and discovered "
          f"listings ({t.ranked} with a year of prices). Not used by research or verdicts.")
    if not t.rows:
        print("  Nothing to rank: watch or discover some listings first.")
        return 0
    print(f"  {'symbol':<10}{'name':<26}{'12-1 mom':>9}{'rank':>6}{'60d vol':>9}{'calm':>6}  note")
    for r in t.rows:
        print(f"  {r.symbol:<10}{cut(r.name, 25):<26}{_pct(r.momentum, True):>9}{_rank(r.momentum_pct):>6}"
              f"{_pct(r.volatility):>9}{_rank(r.low_vol_pct):>6}  {r.note or ''}".rstrip())
    print("  rank and calm are percentiles across the set: 100 is the strongest momentum or the calmest price.")
    return 0


def _pct(v: float | None, signed: bool = False) -> str:
    return "n/a" if v is None else f"{v:+.0%}" if signed else f"{v:.0%}"


def _rank(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.0f}"
