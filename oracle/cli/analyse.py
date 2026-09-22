from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.services.analysis_service import Analysis, analyse, analyse_watched
from oracle.services.context import AppContext


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol", nargs="?", help="omit to analyse every watched listing")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.symbol:
        _print(asyncio.run(analyse(ctx, args.symbol)))
        return 0
    runs = asyncio.run(analyse_watched(ctx))
    if not runs:
        print("nothing watched: add listings with `oracle watch add SYMBOL`")
        return 0
    for r in runs:
        if r.error:
            print(f"{r.symbol}: FAILED ({r.error})")
        else:
            _print(r.analysis)
    return 1 if any(r.error for r in runs) else 0


def _print(result: Analysis) -> None:
    print(result.symbol)
    for s in result.signals:
        print(f"  {s.analyst:<12}{s.direction.value:<9}score {s.score:+.2f}  confidence {s.confidence:.0%}"
              f"  (data to {s.as_of.isoformat()})")
        print(textwrap.fill(s.reason, width=88, initial_indent=" " * 4, subsequent_indent=" " * 4))
    for k in result.skipped:
        print(f"  {k.analyst:<12}no signal: {k.reason}")
