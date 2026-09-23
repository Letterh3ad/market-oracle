from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.tick_service import TickRun, last_tick, run_tick


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "prices, FX, fills, analysis and the strategy step, in that order"
    parser.add_argument("--last", action="store_true", help="show the last tick instead of running one")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.last:
        found = last_tick(ctx)
        if found is None:
            print("no tick has run yet: run `oracle tick`")
        else:
            print_tick(found)
        return 0
    done = asyncio.run(run_tick(ctx))
    print_tick(done)
    return 1 if done.outcome == "failed" else 0


def print_tick(t: TickRun) -> None:
    print(f"tick {t.started_at:%Y-%m-%d %H:%M} UTC ({t.trigger}): {t.outcome}")
    if t.note:
        print(f"  {t.note}")
    for s in t.stages:
        print(f"  {s.name:<9}{s.state:<9}{s.summary}")
        if s.error:
            print(f"  {'':<18}{s.error}")
