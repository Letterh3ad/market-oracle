from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.peers_service import peer_group


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    group = asyncio.run(peer_group(ctx, args.symbol))
    print(f"Peers of {group.symbol}: {group.basis}, {len(group.peers)} peers, biggest first")
    print(f"  {'symbol':<10}{'name':<28}{'size':<11}{'P/E':>8}{'P/B':>8}{'margin':>9}{'growth':>9}")
    for p in group.peers:
        head = f"  {p.symbol:<10}{p.name[:27]:<28}{p.market_cap_band or 'n/a':<11}"
        f = p.fundamentals
        if f is None:
            print(f"{head}{p.note}")
            continue
        print(f"{head}{_num(f.pe):>8}{_num(f.pb):>8}{_pct(f.profit_margin):>9}{_pct(f.revenue_growth):>9}")
    return 0


def _num(v: float | None) -> str:
    return f"{v:.1f}" if v is not None else "n/a"


def _pct(v: float | None) -> str:
    return f"{v:.0%}" if v is not None else "n/a"
