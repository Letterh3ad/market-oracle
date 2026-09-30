from __future__ import annotations

import argparse
import asyncio
import time

from oracle.services.context import AppContext
from oracle.services.history_service import (
    NEWS_START, NOISY, PRICE_START, THIN, Coverage, backfill_history, clear_search_name, coverage, history_companies,
    set_search_name,
)
from oracle.services.universe_service import resolve_symbol
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
                                "the app's live work or scheduled ticks: start it from the app to have it step aside")
    backfill.add_argument("--list", default=DEFAULT_LIST, help=f"watchlist name (default {DEFAULT_LIST})")
    backfill.set_defaults(handler=run_backfill)
    cover = sub.add_parser("coverage", description="what is stored per company and quarter, how much of it names "
                                                   "the company, and how much is left")
    cover.add_argument("--list", default=DEFAULT_LIST, help=f"watchlist name (default {DEFAULT_LIST})")
    cover.set_defaults(handler=run_coverage)
    name = sub.add_parser("name", description="the name a company's news is searched by; with --query or --skip, "
                                              "set it (its news found so far is asked again on the next backfill)")
    name.add_argument("symbol")
    name.add_argument("--query", help='quoted names joined by OR, context words after them: \'"MAN Truck" OR "MAN SE"\'')
    name.add_argument("--alias", action="append", default=[],
                      help="a name headlines about it use, for the noise measure (repeatable; default: the quoted names)")
    name.add_argument("--skip", action="store_true", help="do not search its news at all")
    name.add_argument("--clear", action="store_true", help="go back to the default name")
    name.set_defaults(handler=run_name)


def run_backfill(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(backfill_history(ctx, watchlist=args.list, on_progress=_every(PROGRESS_S)))
    print(f"{r.companies} companies, {r.seconds:.0f} s: {r.summary}; dropped {r.undated} undated and {r.outside} "
          "dated outside the window asked for")
    for f in r.failures[:20]:
        print(f"  failed: {f}")
    if len(r.failures) > 20:
        print(f"  ... and {len(r.failures) - 20} more failures")
    if r.stopped:
        print("run it again to resume")
    return 1 if (r.stopped and r.stopped != "cancelled") or r.failures else 0


def run_coverage(ctx: AppContext, args: argparse.Namespace) -> int:
    print_coverage(coverage(ctx, args.list))
    return 0


def run_name(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.clear:
        symbol, forgotten = clear_search_name(ctx, args.symbol)
        print(f"{symbol}: back to the default name; {forgotten} news windows will be asked again")
        return 0
    if args.query is not None or args.skip:
        symbol, forgotten = set_search_name(ctx, args.symbol, args.query or "", args.alias, args.skip)
        print(f"{symbol}: saved; {forgotten} news windows will be asked again")
    primary = resolve_symbol(ctx, args.symbol).symbol
    found = next((c for c in history_companies(ctx) if c.symbol.casefold() == primary.casefold()), None)
    if found is None:
        print(f"{primary} is not on the main list or its maps")
        return 0
    s = found.search
    print(f"{found.symbol}: " + ("not searched" if s.skip else s.query) + f" ({s.source})"
          + (f"; aliases {', '.join(s.aliases)}" if s.aliases else "") + (f"; {s.note}" if s.note else ""))
    return 0


def print_coverage(c: Coverage) -> None:
    print(f"History coverage from {c.start}: news articles/filings per quarter; * fewer than {THIN} news articles, "
          f"! under {NOISY:.0%} of headlines name the company")
    print(f"{c.windows_done} news windows done, {c.windows_left} windows or companies left to fetch")
    print(f"Last run: {c.last_run.finished_at} {c.last_run.summary}" if c.last_run else "No backfill has run yet")
    print(f"Caveat: {c.caveat}")
    print(f"{'company':<12} {'role':<14} {'bars from':<11} " + " ".join(f"{q:>9}" for q in c.quarters))
    for r in c.rows:
        role = "main list" if r.main else f"map of {','.join(r.of)}"
        cells = []
        for q in c.quarters:
            cell = r.cells.get(q)
            text = f"{cell.news}/{cell.filings}" if cell else "0/0"
            mark = ("!" if q in r.noisy else "") + ("*" if q in r.thin else "")
            cells.append(f"{text + mark:>9}")
        print(f"{r.symbol:<12} {role[:14]:<14} {str(r.first_bar or 'none'):<11} " + " ".join(cells))
        print(f"{'':<12} searched as " + ("nothing" if r.search.skip else r.search.query) + f" ({r.search.source})"
              + (f"; {r.search.note}" if r.search.note else ""))


def _every(seconds: float):
    last = [0.0]

    def say(text: str) -> None:
        now = time.monotonic()
        if now - last[0] >= seconds:
            last[0] = now
            print(f"  {text}", flush=True)

    return say
