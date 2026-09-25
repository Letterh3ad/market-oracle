from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.events_service import HORIZONS, earnings_study


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    study = asyncio.run(earnings_study(ctx, args.symbol))
    print(f"How {study.symbol} moved after past earnings (stored closes)")
    if study.note:
        print(f"  {study.note}")
    if not study.events:
        return 0
    bench = f"; against {study.benchmark}" if study.benchmark else "; no benchmark prices stored"
    print(f"  dates from {study.source}, fetched {study.fetched_at[:10]}{bench}")
    if study.headline:
        print(f"  {study.headline}")
    for s in study.summaries:
        if s.n:
            vs = f", median {_pct(s.median_excess)} vs benchmark, beat it {s.beat} times" if s.median_excess is not None else ""
            print(f"  {s.days:>2} day{'s' if s.days != 1 else ' '}: median {_pct(s.median)}, {s.up} of {s.n} up{vs}")
    print()
    print(f"  {'release':<12}{'timing':<14}{'from close':<12}" + "".join(f"{f'{h}d':>9}{'vs':>8}" for h in HORIZONS))
    for e in study.events:
        timing = {True: "before open", False: "after open", None: "time unknown"}[e.before_open]
        head = f"  {e.day.isoformat():<12}{timing:<14}{e.base.isoformat() if e.base else '':<12}"
        if e.note:
            print(f"{head}{e.note}")
            continue
        print(head + "".join(f"{_pct(w.move):>9}{_pct(w.excess):>8}" for w in e.windows))
    return 0


def _pct(v: float | None) -> str:
    return f"{v * 100:+.1f}%" if v is not None else "n/a"
