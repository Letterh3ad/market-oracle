from __future__ import annotations

import argparse
import asyncio
import time

from oracle.services.context import AppContext
from oracle.services.history_service import NEWS_START, PRICE_START, THIN, Coverage, backfill_history, coverage
from oracle.services.watch_service import DEFAULT_LIST

PROGRESS_S = 30.0


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("news, filings and price history back in time for a watchlist and each one's industry "
                          "map, for testing whether the news predicted anything")
    sub = parser.add_subparsers(dest="history_command", required=True)
    backfill = sub.add_parser(
        "backfill", description=f"fetch what is not stored yet: daily bars from {PRICE_START}, SEC 8-K/10-Q/10-K "
                                f"filings (US names) and Google News quarter by quarter from {NEWS_START}. Hours "
                                "for a whole list; stop it any time, the next run resumes. It does not wait for "
                                "the app's live work: start it from the app to have it step aside")
    backfill.add_argument("--list", default=DEFAULT_LIST, help=f"watchlist name (default {DEFAULT_LIST})")
    backfill.set_defaults(handler=run_backfill)
    cover = sub.add_parser("coverage", description="what is stored per company and quarter, and how much is left")
    cover.add_argument("--list", default=DEFAULT_LIST, help=f"watchlist name (default {DEFAULT_LIST})")
    cover.set_defaults(handler=run_coverage)


def run_backfill(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(backfill_history(ctx, watchlist=args.list, on_progress=_every(PROGRESS_S)))
    print(f"{r.companies} companies, {r.requests} requests in {r.seconds:.0f} s: {r.windows} windows finished, "
          f"{r.kept} articles and filings kept, {r.prices_added} price bars added; dropped {r.undated} undated "
          f"and {r.outside} dated outside the window asked for")
    for f in r.failures[:20]:
        print(f"  failed: {f}")
    if len(r.failures) > 20:
        print(f"  ... and {len(r.failures) - 20} more failures")
    if r.stopped:
        print(f"stopped early: {r.stopped}; run it again to resume")
    return 0


def run_coverage(ctx: AppContext, args: argparse.Namespace) -> int:
    print_coverage(coverage(ctx, args.list))
    return 0


def print_coverage(c: Coverage) -> None:
    print(f"History coverage from {c.start}: news articles/filings per quarter; * fewer than {THIN} news articles")
    print(f"{c.windows_done} news windows done, {c.windows_left} windows or companies left to fetch")
    print(f"Caveat: {c.caveat}")
    print(f"{'company':<12} {'role':<14} {'bars from':<11} " + " ".join(f"{q:>9}" for q in c.quarters))
    for r in c.rows:
        role = "main list" if r.main else f"map of {','.join(r.of)}"
        cells = []
        for q in c.quarters:
            cell = r.cells.get(q)
            text = f"{cell.news}/{cell.filings}" if cell else "0/0"
            cells.append(f"{text + ('*' if q in r.thin else ''):>9}")
        print(f"{r.symbol:<12} {role[:14]:<14} {str(r.first_bar or 'none'):<11} " + " ".join(cells))


def _every(seconds: float):
    last = [0.0]

    def say(text: str) -> None:
        now = time.monotonic()
        if now - last[0] >= seconds:
            last[0] = now
            print(f"  {text}", flush=True)

    return say
