from __future__ import annotations

import argparse
import asyncio
from datetime import date

from oracle.cli.history import PROGRESS_S, _every
from oracle.services.context import AppContext
from oracle.services.testbed_service import START, UNDER_PRICED, TestbedCoverage, build_testbed, testbed_coverage


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("the signal testbed: S&P 500 members as they were on each day, their daily bars from "
                          f"{START} and sectors, kept in the research database (research_db_path), never the live one")
    sub = parser.add_subparsers(dest="testbed_command", required=True)
    build = sub.add_parser("build", description="fetch what the testbed lacks: the membership history, sectors and "
                                                "bars of every member not tried yet. Hours the first time; stop it any "
                                                "time, the next run resumes. It does not wait for the app's live work: "
                                                "start it from the app to have it step aside")
    build.add_argument("--refresh", action="store_true",
                       help="read the membership history again and top up the bars of current members")
    build.add_argument("--only", nargs="+", metavar="TICKER", help="only these members (a smoke run)")
    build.add_argument("--limit", type=int, help="only the first N members, alphabetically (a smoke run)")
    build.add_argument("--from", dest="start", type=date.fromisoformat, default=START,
                       help=f"first bar day (default {START})")
    build.add_argument("--to", dest="end", type=date.fromisoformat, help="last bar day (a smoke run; default today)")
    build.set_defaults(handler=run_build)
    status = sub.add_parser("status", description="member-days with and without bars per year (the missing share), "
                                                  "the members never priced, and the last build")
    status.set_defaults(handler=run_status)
    aliases = sub.add_parser("aliases", description="renamed members priced from the ticker they became (accepted only "
                                                    "when SEC and Yahoo agree), and the pairs kept for review")
    aliases.add_argument("--verify", action="store_true",
                         help="check renames and reused tickers with SEC and Yahoo again (needs [research] contact)")
    aliases.set_defaults(handler=run_aliases)


def run_build(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(build_testbed(ctx, refresh=args.refresh, only=args.only, limit=args.limit, start=args.start,
                                  end=args.end, on_progress=_every(PROGRESS_S)))
    print(f"{r.seconds:.0f} s, membership {r.snapshot}: {r.summary}")
    for f in r.failures[:20]:
        print(f"  failed: {f}")
    if r.stopped:
        print("run it again to resume")
    return 1 if (r.stopped and r.stopped != "cancelled") or r.failures else 0


def run_aliases(ctx: AppContext, args: argparse.Namespace) -> int:
    code = 0
    if args.verify:
        r = asyncio.run(build_testbed(ctx, verify=True, on_progress=_every(PROGRESS_S)))
        print(f"{r.seconds:.0f} s: {r.summary}")
        code = 1 if (r.stopped and r.stopped != "cancelled") or r.failures else 0
    print_aliases(testbed_coverage(ctx))
    return code


def print_aliases(c: TestbedCoverage) -> None:
    accepted = [a for a in c.aliases if a.accepted]
    print(f"Renames checked {c.renames_checked or 'never'}: {len(accepted)} accepted, "
          f"{len(c.aliases) - len(accepted)} kept for review (never applied)")
    for a in c.aliases:
        print(f"  {a.old:<6} -> {a.new:<6} {str(a.effective_day or ''):<10} {a.evidence}")


def run_status(ctx: AppContext, args: argparse.Namespace) -> int:
    print_coverage(testbed_coverage(ctx), ctx.config.research_db_path)
    return 0


def print_coverage(c: TestbedCoverage, where) -> None:
    print(f"Signal testbed in {where}")
    s = c.snapshot
    print(f"Membership: {s.source} at {s.revision or 'unknown revision'}, fetched {s.fetched_at[:16]} UTC, "
          f"{s.changes} rows {s.first_day} to {s.last_day}" if s else "Membership: not fetched yet")
    if s and s.last_day < c.as_of:
        print(f"  Its last change is {s.last_day}: to {c.as_of} the members are that day's list; later changes "
              "arrive when the source adds them and a refresh reads them")
    print(f"Last build: {c.last_run.finished_at} {c.last_run.summary}" if c.last_run else "Last build: none yet")
    print(f"{c.members} members from {c.start}, {c.fetched} fetched, {c.sectors} with a sector; "
          f"trading days from {c.calendar}; missing share {c.missing_share:.1%}, "
          f"{c.missing_share_mapped:.1%} with renames mapped")
    print(f"Caveat: {c.caveat}")
    print(f"{'year':<6} {'members':>7} {'member-days':>11} {'with bars':>10} {'missing':>8} {'mapped':>8} "
          f"{'no bars':>8}")
    for y in c.years:
        print(f"{y.year:<6} {y.members:>7} {y.member_days:>11} {y.with_bars:>10} {y.missing_share:>8.1%} "
              f"{y.missing_share_mapped:>8.1%} {y.unpriced:>8}")
    print(f"{len(c.under_priced)} members priced on under {UNDER_PRICED:.0%} of their member-days:")
    for u in c.under_priced:
        print(f"  {u.ticker:<8} {u.first_day} to {u.last_day}, {u.member_days} member-days: {u.note}")
    for d in c.duplicates:
        print(f"Listed twice: {' and '.join(d.tickers)}, {d.first_day} to {d.last_day} ({d.days} days): {d.why}")
    accepted = sum(a.accepted for a in c.aliases)
    print(f"Renames: {accepted} accepted, {len(c.aliases) - accepted} kept for review; `oracle testbed aliases` "
          "lists them")
