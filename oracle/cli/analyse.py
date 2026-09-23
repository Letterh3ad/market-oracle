from __future__ import annotations

import argparse
import asyncio

from oracle.cli.common import print_signal
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
        print_signal(s, 2)
    for k in result.skipped:
        print(f"  {k.analyst:<12}no signal: {k.reason}")
    lean = result.lean
    if lean is None:
        print("  baseline lean: none (needs the technicals signal)")
    else:
        print(f"  {lean.source} lean: {lean.label} ({lean.probability:.0%})")
