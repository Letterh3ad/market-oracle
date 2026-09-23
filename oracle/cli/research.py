from __future__ import annotations

import argparse
import asyncio
import textwrap
from datetime import date

from oracle.services.context import AppContext
from oracle.services.research_service import ResearchRun, RunStatus, research


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol", help="the listing to research")
    parser.add_argument("--as-of", type=_day, default=None, metavar="YYYY-MM-DD",
                        help="replay that day: only news and prices from by its end are used")
    parser.set_defaults(handler=run)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date like 2026-09-18: {text!r}") from exc


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    print_run(asyncio.run(research(ctx, args.symbol, as_of=args.as_of)), ctx.config.llm.max_steps)
    return 0


def print_run(r: ResearchRun, max_steps: int) -> None:
    replay = f", as of {r.as_of.isoformat()}" if r.as_of else ""
    print(f"{r.symbol}{replay}, researched {r.started_at}")
    for s in r.steps:
        if s.action == "final":
            continue
        print(f"{s.n}. {s.thought or '(no thought)'}")
        if s.action:
            print(f"   {s.action}({s.input})")
        for line in (s.observation or "").splitlines():
            print(textwrap.indent(textwrap.fill(line, 96), "   > "))
    if r.status == RunStatus.STEP_CAP:
        print(f"stopped at the {max_steps}-step limit; findings asked for then")
    print(f"Findings ({r.backend} {r.model}):")
    print(textwrap.indent(textwrap.fill(r.findings or "(none given)", 96), "  "))
    cited = [e for e in r.evidence if e.cited]
    if cited:
        print("Evidence cited:")
    for e in cited:
        day = e.published.date().isoformat() if e.published else "undated"
        print(f"  [a{e.id}] {day} {e.outlet or 'unknown outlet'}: {e.title}")
        print(f"        {e.url}")
