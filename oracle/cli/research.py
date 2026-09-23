from __future__ import annotations

import argparse
import asyncio
import textwrap
from datetime import date

from oracle.services.context import AppContext
from oracle.services.research_service import (
    UNVALIDATED, Claim, Report, ResearchRun, RunStatus, report_history, research,
)

WIDTH = 96


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol", help="the listing to research")
    parser.add_argument("--as-of", type=_day, default=None, metavar="YYYY-MM-DD",
                        help="replay that day: only news and prices from by its end are used")
    parser.add_argument("--history", action="store_true", help="list past reports instead of researching")
    parser.set_defaults(handler=run)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date like 2026-09-18: {text!r}") from exc


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.history:
        print_history(report_history(ctx, args.symbol), args.symbol)
        return 0
    print_run(asyncio.run(research(ctx, args.symbol, as_of=args.as_of)), ctx.config.llm.max_steps)
    return 0


def print_run(r: ResearchRun, max_steps: int) -> None:
    replay = f", as of {r.as_of.isoformat()}" if r.as_of else ""
    print(f"{r.symbol}{replay}, researched {r.started_at}")
    if r.queries:
        print(f"Searches: {'; '.join(r.queries)}")
    for s in r.steps:
        if s.action == "final":
            continue
        print(f"{s.n}. {s.thought or '(no thought)'}")
        if s.action:
            print(f"   {s.action}({s.input})")
        for line in (s.observation or "").splitlines():
            print(textwrap.indent(textwrap.fill(line, WIDTH), "   > "))
    if r.status == RunStatus.STEP_CAP:
        print(f"stopped at the {max_steps}-step limit; findings asked for then")
    print(f"Findings ({r.backend} {r.model}):")
    _wrapped(r.findings or "(none given)", "  ")
    if r.report:
        print_report(r.report)
    cited = [e for e in r.evidence if e.cited]
    if cited:
        print("Evidence cited:")
    for e in cited:
        day = e.published.date().isoformat() if e.published else "undated"
        print(f"  [a{e.id}] {day} {e.outlet or 'unknown outlet'}: {e.title}")
        print(f"        {e.url}")


def print_report(report: Report) -> None:
    print(f"Report ({UNVALIDATED}):")
    for label, case in (("Bull", report.bull), ("Bear", report.bear)):
        _wrapped(f"{label}: {_claim(case) if case else '(no case the evidence supports)'}", "  ")
    if report.findings:
        print("  Findings:")
    for c in report.findings:
        _wrapped(f"- {c.role}: {_claim(c)}", "    ")
    if report.risks:
        print("  Risks:")
    for c in report.risks:
        _wrapped(f"- {_claim(c)}", "    ")
    if report.invalidated_if:
        _wrapped(f"Invalidated if: {report.invalidated_if}", "  ")


def print_history(runs: list[ResearchRun], symbol: str) -> None:
    if not runs:
        print(f"no research reports for {symbol.upper()} yet: run `oracle research {symbol}`")
    for r in runs:
        replay = " (replay)" if r.as_of else ""
        print(f"{r.report.day.isoformat()}{replay}  {r.backend} {r.model}")
        for label, case in (("bull", r.report.bull), ("bear", r.report.bear)):
            _wrapped(f"{label}: {case.text if case else '(none)'}", "  ")


def _claim(c: Claim) -> str:
    return f"{c.text} {' '.join(f'[a{i}]' for i in c.cites)}"


def _wrapped(text: str, indent: str) -> None:
    print(textwrap.fill(text, WIDTH, initial_indent=indent, subsequent_indent=indent + "  "))
