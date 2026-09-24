from __future__ import annotations

import argparse
import json

from oracle.cli.common import iso_day
from oracle.services.context import AppContext
from oracle.services.replay_service import (
    StudyStatus, create_study, pause_study, retry_failed, start_study, study_status,
)


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("Research replayed at past dates over a fixed spread of listings, one replay at a time "
                          "while `oracle gui` runs and the PC is free. It never starts by itself.")
    sub = parser.add_subparsers(dest="replay_command", required=True)
    create = sub.add_parser("create", help="pick the listings and dates; the study starts paused")
    create.add_argument("--from", dest="first", type=iso_day, default=None, metavar="YYYY-MM-DD",
                        help="the first replay day (default: the model's measured cutoff)")
    create.set_defaults(handler=_create)
    sub.add_parser("start", help="let replays run in free time").set_defaults(handler=_start)
    sub.add_parser("pause", help="no new replays; a running one finishes").set_defaults(handler=_pause)
    status = sub.add_parser("status", help="progress, failures and the mean replay time")
    status.add_argument("--json", action="store_true", help="print the status as JSON")
    status.set_defaults(handler=_status)
    retry = sub.add_parser("retry", help="put failed cells back in the queue")
    retry.add_argument("cell", type=int, nargs="?", default=None, help="one cell's id (default: every failed cell)")
    retry.set_defaults(handler=_retry)


def _create(ctx: AppContext, args: argparse.Namespace) -> int:
    s = create_study(ctx, args.first)
    print(f"Created replay study {s.id}: {len(s.listings)} listings, {s.total} replays from "
          f"{s.first_day.isoformat()} to {s.last_day.isoformat()}. It is paused: `oracle replay start` runs it.")
    print_listings(s)
    return 0


def _start(ctx: AppContext, args: argparse.Namespace) -> int:
    start_study(ctx)
    print("Started: replays run one at a time while `oracle gui` runs and nothing else needs the PC.")
    return 0


def _pause(ctx: AppContext, args: argparse.Namespace) -> int:
    pause_study(ctx)
    print("Paused: no new replays start; one already running finishes.")
    return 0


def _retry(ctx: AppContext, args: argparse.Namespace) -> int:
    n = retry_failed(ctx, args.cell)
    print(f"{n} failed cell{'' if n == 1 else 's'} back in the queue.")
    return 0


def _status(ctx: AppContext, args: argparse.Namespace) -> int:
    s = study_status(ctx)
    if args.json:
        print(json.dumps(s.to_json() if s else None, indent=2))
    elif s is None:
        print("No replay study yet: `oracle replay create --from YYYY-MM-DD`.")
    else:
        print_status(s)
    return 0


def print_listings(s: StudyStatus) -> None:
    for p in s.listings:
        print(f"  {p.symbol:<12}{p.region:<10}{p.sector:<24}{p.market_cap or ''}  {p.name}")


def print_status(s: StudyStatus) -> None:
    state = s.state + (f" ({s.paused_reason})" if s.paused_reason else "")
    print(f"Replay study {s.id}, {s.first_day.isoformat()} to {s.last_day.isoformat()}: {state}")
    print(f"  {s.done} done, {s.pending} queued ({s.retrying} to retry), {len(s.running)} running, "
          f"{len(s.failed)} failed of {s.total}")
    print(f"  listings: {', '.join(p.symbol for p in s.listings)}")
    mean = f"{s.mean_replay_s / 60:.1f} min" if s.mean_replay_s is not None else "none finished yet"
    print(f"  mean replay time: {mean}")
    for c in s.running:
        print(f"  running: {c.symbol} as of {c.as_of.isoformat()} for {c.age_s / 60:.0f} min")
    for reason, n in s.top_failures:
        print(f"  {n} x {reason}")
    for c in s.failed:
        print(f"  failed cell {c.id}: {c.symbol} as of {c.as_of.isoformat()}: {c.error}")
    if s.complete:
        print("  complete: every replay has run")
