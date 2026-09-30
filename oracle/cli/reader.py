from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.reader_service import plan_reader, score_reader, waits_anywhere
from oracle.services.watch_service import DEFAULT_LIST


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("The stronger reader: a local Ollama model ([laya] reader_model) reads each backfilled "
                          "headline once for its own company, for the predictive test")
    sub = parser.add_subparsers(dest="reader_command", required=True)
    run = sub.add_parser("run", help="read every backfilled headline the model has not read (hours; resumes)")
    run.add_argument("--list", default=DEFAULT_LIST, help="the list whose history is read (default main)")
    run.set_defaults(handler=run_cmd)
    plan = sub.add_parser("plan", help="how many headlines a run would read and how long it would take")
    plan.add_argument("--list", default=DEFAULT_LIST)
    plan.set_defaults(handler=plan_cmd)


def run_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    run = score_reader(ctx, args.list, busy=lambda: waits_anywhere(ctx), on_progress=lambda line: print(line, flush=True))
    print(run.summary)
    return 1 if run.stopped else 0


def plan_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    p = plan_reader(ctx, args.list)
    print(f"{p.model}: {p.unscored} of {p.subject_articles} headlines to read ({p.pairs} pairs over {p.groups} groups); "
          f"about {p.estimate_s / 3600:.1f} h at {p.rate} a second ({p.basis})")
    return 0
