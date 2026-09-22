from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.services.analysis_service import analyse
from oracle.services.context import AppContext


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    result = asyncio.run(analyse(ctx, args.symbol))
    print(result.symbol)
    for s in result.signals:
        print(f"  {s.analyst:<12}{s.direction.value:<9}score {s.score:+.2f}  confidence {s.confidence:.0%}"
              f"  (data to {s.as_of.isoformat()})")
        print(textwrap.fill(s.reason, width=88, initial_indent=" " * 4, subsequent_indent=" " * 4))
    for k in result.skipped:
        print(f"  {k.analyst:<12}no signal: {k.reason}")
    return 0
