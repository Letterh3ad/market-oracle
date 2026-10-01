from __future__ import annotations

import argparse
import asyncio
from datetime import date

from oracle.cli.history import PROGRESS_S, _every
from oracle.services.context import AppContext
from oracle.services.testbed_service import START, UNDER_PRICED, TestbedCoverage, build_testbed, testbed_coverage
from oracle.services.insider_service import InsiderCoverage, fetch_insiders, insider_coverage
from oracle.services.xbrl_service import XbrlCoverage, fetch_fundamentals, fundamentals_coverage


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
    facts = sub.add_parser("fundamentals", description="fetch the priced members' SEC XBRL facts, their filings' "
                                                       "acceptance times and Yahoo splits into the research database "
                                                       "(about an hour the first time; it resumes). It does not wait for "
                                                       "the app's live work: start it from the app to have it step aside")
    facts.add_argument("--refresh", action="store_true", help="fetch every member again (new filings)")
    facts.add_argument("--only", nargs="+", metavar="TICKER", help="only these members (a smoke run)")
    facts.add_argument("--limit", type=int, help="only the first N members, alphabetically (a smoke run)")
    facts.set_defaults(handler=run_fundamentals)
    cover = sub.add_parser("fundamentals-status", description="per year: priced members, those with a CIK and with "
                                                              "facts, and per signal the members with a scored filing")
    cover.set_defaults(handler=run_fundamentals_status)
    ins = sub.add_parser("insiders", description="fetch the priced members' Form 4 and 8-K filing times and 8-K items "
                                                 "(SEC submissions) and their Form 4 open-market trades (SEC's quarterly "
                                                 "data sets, 2009 on) into the research database; it resumes. It does "
                                                 "not wait for the app's live work: start it from the app for that")
    ins.add_argument("--refresh", action="store_true", help="every issuer's filings and every quarter again")
    ins.add_argument("--only", nargs="+", metavar="TICKER", help="only these members (a smoke run)")
    ins.add_argument("--limit", type=int, help="only the first N members, alphabetically (a smoke run)")
    ins.set_defaults(handler=run_insiders)
    ins_cover = sub.add_parser("insiders-status", description="per year: open-market purchases and sales, Form 4s, "
                                                              "8-Ks and items 2.02, 1.01, 2.01; quarters stored")
    ins_cover.set_defaults(handler=run_insiders_status)


def run_build(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(build_testbed(ctx, refresh=args.refresh, only=args.only, limit=args.limit, start=args.start,
                                  end=args.end, on_progress=_every(PROGRESS_S)))
    print(f"{r.seconds:.0f} s, membership {r.snapshot}: {r.summary}")
    for f in r.failures[:20]:
        print(f"  failed: {f}")
    if r.stopped:
        print("run it again to resume")
    return 1 if (r.stopped and r.stopped != "cancelled") or r.failures else 0


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
          f"trading days from {c.calendar}; missing share {c.missing_share:.1%}")
    print(f"Caveat: {c.caveat}")
    print(f"{'year':<6} {'members':>7} {'member-days':>11} {'with bars':>10} {'missing':>8} {'no bars':>8}")
    for y in c.years:
        print(f"{y.year:<6} {y.members:>7} {y.member_days:>11} {y.with_bars:>10} {y.missing_share:>8.1%} "
              f"{y.unpriced:>8}")
    print(f"{len(c.under_priced)} members priced on under {UNDER_PRICED:.0%} of their member-days:")
    for u in c.under_priced:
        print(f"  {u.ticker:<8} {u.first_day} to {u.last_day}, {u.member_days} member-days: {u.note}")
    for d in c.duplicates:
        print(f"Listed twice: {' and '.join(d.tickers)}, {d.first_day} to {d.last_day} ({d.days} days): {d.why}")


def run_fundamentals(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(fetch_fundamentals(ctx, refresh=args.refresh, only=args.only, limit=args.limit,
                                       on_progress=_every(PROGRESS_S)))
    print(f"{r.seconds:.0f} s: {r.summary}")
    for f in r.failures[:20]:
        print(f"  failed: {f}")
    if r.stopped:
        print("run it again to resume")
    return 1 if (r.stopped and r.stopped != "cancelled") or r.failures else 0


def run_fundamentals_status(ctx: AppContext, args: argparse.Namespace) -> int:
    print_fundamentals(fundamentals_coverage(ctx))
    return 0


def print_fundamentals(c: XbrlCoverage) -> None:
    print(f"SEC fundamentals: {c.priced} priced members, {c.with_cik} with a CIK, {c.with_facts} with facts; "
          f"SEC ticker map read {c.tickers_fetched_at or 'never'}")
    print(f"Last fetch: {c.last_run.finished_at} {c.last_run.summary}" if c.last_run else "Last fetch: none yet")
    names = list(c.years[0].scored) if c.years else []
    print(f"{'year':<6} {'members':>7} {'CIK':>5} {'facts':>6}  " + "  ".join(f"{n:>24}" for n in names))
    for y in c.years:
        print(f"{y.year:<6} {y.members:>7} {y.with_cik:>5} {y.with_facts:>6}  "
              + "  ".join(f"{f'{y.scored[n]} members, {y.events[n]} filings':>24}" for n in names))
    if c.without_cik:
        print(f"No CIK in SEC's ticker map ({len(c.without_cik)}): {' '.join(c.without_cik)}")
    if c.without_facts:
        print(f"No XBRL facts at SEC ({len(c.without_facts)}): {' '.join(c.without_facts)}")


def run_insiders(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(fetch_insiders(ctx, refresh=args.refresh, only=args.only, limit=args.limit,
                                   on_progress=_every(PROGRESS_S)))
    print(f"{r.seconds:.0f} s: {r.summary}")
    for f in r.failures[:20]:
        print(f"  failed: {f}")
    if r.stopped:
        print("run it again to resume")
    return 1 if (r.stopped and r.stopped != "cancelled") or r.failures else 0


def run_insiders_status(ctx: AppContext, args: argparse.Namespace) -> int:
    print_insiders(insider_coverage(ctx))
    return 0


INSIDER_COLUMNS = (("P", "buys"), ("P_issuers", "buying"), ("S", "sales"), ("4", "Form 4s"), ("8-K", "8-Ks"),
                   ("8-K 2.02", "2.02"), ("8-K 1.01", "1.01"), ("8-K 2.01", "2.01"))


def print_insiders(c: InsiderCoverage) -> None:
    q = sorted(c.quarters)
    print(f"Form 4 and 8-K: {c.priced} priced members, {c.issuers} issuers with a CIK ({c.no_cik} members without), "
          f"{c.filings_fetched} issuers' filings fetched; trades for {len(q)} quarters"
          + (f", {q[0]} to {q[-1]}" if q else ""))
    print(f"Last fetch: {c.last_run.finished_at} {c.last_run.summary}" if c.last_run else "Last fetch: none yet")
    print(f"{'year':<6}" + "".join(f"{label:>9}" for _, label in INSIDER_COLUMNS))
    for y, row in c.years.items():
        print(f"{y:<6}" + "".join(f"{row.get(k, 0):>9}" for k, _ in INSIDER_COLUMNS))
