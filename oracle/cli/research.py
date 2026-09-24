from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.cli.common import iso_day
from oracle.services.context import AppContext
from oracle.services.research_service import (
    UNVALIDATED, UNVALIDATED_TAG, Claim, Report, ResearchRun, RunStatus, report_history, research,
)

WIDTH = 96


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol", help="the listing to research")
    parser.add_argument("--as-of", type=iso_day, default=None, metavar="YYYY-MM-DD",
                        help="replay that day: only news and prices from by its end are used")
    parser.add_argument("--history", action="store_true", help="list past reports instead of researching")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.history:
        print_history(report_history(ctx, args.symbol), args.symbol)
        return 0
    r = asyncio.run(research(ctx, args.symbol, as_of=args.as_of))
    print_run(r, ctx.config.llm.max_steps)
    return 1 if r.status == RunStatus.FAILED else 0


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
    print(f"Findings ({r.model_label}):")
    _wrapped(r.findings or "(none given)", "  ")
    if r.status == RunStatus.FAILED:
        print(f"Failed: {r.error}")
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
    _wrapped(f"Verdict {UNVALIDATED_TAG}: {_verdict(report)}", "  ")
    if report.verdict:
        _wrapped(f"Verdict wrong if: {report.verdict.invalidated_if}", "  ")
    if report.did_not_read:
        _wrapped(f"The verdict {report.did_not_read}", "  ")
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
    if report.rejections:
        _wrapped(f"Rejected: {report.rejections}", "  ")


def print_history(runs: list[ResearchRun], symbol: str) -> None:
    if not runs:
        print(f"no research reports for {symbol.upper()} yet: run `oracle research {symbol}`")
    for r in runs:
        replay = " (replay)" if r.as_of else ""
        print(f"{r.report.day.isoformat()}{replay}  {r.model_label}")
        _wrapped(f"verdict {UNVALIDATED_TAG}: {_verdict(r.report)}", "  ")
        for label, case in (("bull", r.report.bull), ("bear", r.report.bear)):
            _wrapped(f"{label}: {case.text if case else '(none)'}", "  ")


def _verdict(report: Report) -> str:
    if v := report.verdict:
        return f"{v.label.upper()}, confidence {v.confidence:.0%}: {_cited(v.reason, v.cites)}"
    return f"none ({report.no_verdict})"


def _claim(c: Claim) -> str:
    return _cited(c.text, c.cites)


def _cited(text: str, cites: tuple[int, ...]) -> str:
    return f"{text} {' '.join(f'[a{i}]' for i in cites)}"


def _wrapped(text: str, indent: str) -> None:
    print(textwrap.fill(text, WIDTH, initial_indent=indent, subsequent_indent=indent + "  "))

