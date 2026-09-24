from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.discovery_service import DiscoveryRun, discover


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("find companies related to the watched ones and add them to the "
                          "\"discovered\" watchlist, up to [discovery] per_tick")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    print_run(asyncio.run(discover(ctx)))
    return 0


def print_run(r: DiscoveryRun) -> None:
    if r.note:
        print(f"Discovery: {r.note}")
        return
    print(f"Discovery from {', '.join(r.seeds)}: added {len(r.added)}")
    for a in r.added:
        print(f"  added {a.symbol}  {a.name}  ({a.why})")
    for s in r.skipped:
        print(f"  skipped {s.name}: {s.reason}")
    for e in r.source_errors:
        print(f"  could not use {e}")
