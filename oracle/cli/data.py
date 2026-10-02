from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.services.context import AppContext
from oracle.services.data_check_service import DataCheckRun, last_data_check, run_data_checks
from oracle.services.data_checks import CHECKS
from oracle.services.errors import InvalidQuery

SHOWN = 10


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "data-error checks over the live and research databases; they flag, never edit"
    sub = parser.add_subparsers(dest="data_command", required=True)
    check = sub.add_parser("check", description="run every check now (a minute or so on the full databases), keep "
                                                "the run and print it; with [data_checks] explain on, a local Ollama "
                                                "model explains the first flags")
    show = sub.add_parser("show", description="print the newest kept run")
    for p in (check, show):
        p.add_argument("--check", metavar="NAME", help=f"only this check: {', '.join(CHECKS)}")
        p.add_argument("--limit", type=int, default=SHOWN, help=f"flagged rows printed per check (default {SHOWN}; "
                                                                 "0 all kept)")
    check.set_defaults(handler=run_check)
    show.set_defaults(handler=run_show)


def run_check(ctx: AppContext, args: argparse.Namespace) -> int:
    _known(args.check)
    print_run(asyncio.run(run_data_checks(ctx, say=lambda stage: print(f"checking the {stage}..."))),
              args.check, args.limit)
    return 0


def run_show(ctx: AppContext, args: argparse.Namespace) -> int:
    _known(args.check)
    run = last_data_check(ctx)
    if run is None:
        print("No data check has run yet: run `oracle data check`.")
        return 0
    print_run(run, args.check, args.limit)
    return 0


def _known(check: str | None) -> None:
    if check is not None and check not in CHECKS:
        raise InvalidQuery(f"no check {check}; the checks are {', '.join(CHECKS)}")


def print_run(run: DataCheckRun, only: str | None = None, limit: int = SHOWN) -> None:
    print(f"Data checks run {run.run_at[:16].replace('T', ' ')} UTC in {run.seconds:g} s: {run.summary}")
    for note in run.notes:
        print(textwrap.fill(note, 100, initial_indent="- ", subsequent_indent="  "))
    for c in run.checks:
        if only and c.code != only:
            continue
        print(f"\n{c.label} ({c.code}): {c.count}")
        rows = c.rows if limit <= 0 else c.rows[:limit]
        for f in rows:
            where = " ".join(x for x in (f.db, f.symbol, f.day or "") if x)
            print(textwrap.fill(f"{where}: {f.detail}", 100, initial_indent="  ", subsequent_indent="    "))
            if f.explanation:
                print(textwrap.fill(f.explanation, 100, initial_indent="    > ", subsequent_indent="      "))
        if (more := c.count - len(rows)) > 0:
            print(f"  ... {more} more" + (" (only the first kept rows are stored)" if len(c.rows) < c.count else ""))
