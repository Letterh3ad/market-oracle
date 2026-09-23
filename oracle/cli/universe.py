from __future__ import annotations

import argparse
import asyncio

from oracle.cli.common import cut
from oracle.services.context import AppContext
from oracle.services.universe_service import (
    FILTER_FIELDS,
    SORTS,
    facets,
    UniverseStatus,
    refresh_universe,
    search_universe,
    universe_status,
)


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="universe_command", required=True)
    sub.add_parser(
        "refresh", help="download FinanceDatabase (~17MB) and replace the local universe"
    ).set_defaults(handler=refresh)
    sub.add_parser("status", help="what is loaded and when it was refreshed").set_defaults(handler=status)
    search = sub.add_parser(
        "search", help="find companies by name or symbol, or browse with filters"
    )
    search.add_argument("query", nargs="?", default="", help="words to match; omit to browse")
    for name in FILTER_FIELDS:
        search.add_argument(
            f"--{_flag(name)}", dest=name, action="append", default=[], metavar="VALUE",
            help="repeat for either; see `oracle universe facets`",
        )
    search.add_argument(
        "--tag", dest="tags", action="append", default=[], metavar="TAG",
        help="repeat to require every tag",
    )
    search.add_argument("--sort", choices=[_flag(s) for s in SORTS], help="default: relevance, or size when browsing")
    search.add_argument("--desc", action="store_true", help="reverse the sort (not relevance)")
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--all-listings", action="store_true", help="every exchange listing, not one per company")
    search.add_argument("--include-delisted", action="store_true")
    search.add_argument("--limit", type=int, default=20, help="at most 500")
    search.set_defaults(handler=search_cmd)
    facet = sub.add_parser("facets", help="the values a filter accepts, with company counts")
    facet.add_argument("field", choices=[_flag(f) for f in FILTER_FIELDS])
    facet.set_defaults(handler=facets_cmd)


def refresh(ctx: AppContext, args: argparse.Namespace) -> int:
    print("downloading FinanceDatabase...", flush=True)
    _print(asyncio.run(refresh_universe(ctx)))
    return 0


def status(ctx: AppContext, args: argparse.Namespace) -> int:
    _print(universe_status(ctx))
    return 0


def search_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    hits = search_universe(
        ctx, args.query,
        filters={name: getattr(args, name) for name in FILTER_FIELDS}, tags=args.tags,
        sort=args.sort and _field(args.sort), descending=args.desc,
        all_listings=args.all_listings, include_delisted=args.include_delisted,
        limit=args.limit, offset=args.offset,
    )
    if not hits:
        print("no matches")
        return 0
    print(f"{'symbol':<12}{'name':<32}{'country':<16}{'exch':<6}{'sector / category':<24}size")
    for h in hits:
        notes = [n for n in (
            _others(h.listings - 1) if h.listings > 1 and not args.all_listings else "",
            "delisted" if h.delisted else "",
            "no price data lately" if h.dead else "",
            h.type if h.type != "equity" else "",
            f"[{', '.join(h.tags)}]" if h.tags else "",
        ) if n]
        size = (h.market_cap or "?").removesuffix(" Cap")
        print(
            f"{h.symbol:<12}{cut(h.name, 31):<32}{cut(h.country, 15):<16}{h.exchange or '':<6}"
            f"{cut(h.sector or h.category, 23):<24}{size:<7}{'  '.join(notes)}".rstrip()
        )
    return 0


def facets_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = facets(ctx, _field(args.field))
    width = max((len(f.value) for f in rows), default=5) + 2
    print(f"{args.field:<{width}}{'companies':>9}")
    for f in rows:
        print(f"{f.value:<{width}}{f.companies:>9}  {f.label or ''}".rstrip())
    return 0


def _flag(name: str) -> str:
    return name.replace("_", "-")


def _field(flag: str) -> str:
    return flag.replace("-", "_")


def _others(n: int) -> str:
    return f"+{n} listing" + ("s" if n > 1 else "")


def _print(s: UniverseStatus) -> None:
    if not s.loaded:
        print("universe not loaded yet: run `oracle universe refresh`")
        return
    print(f"{s.listings} listings, {s.companies} companies, {s.delisted} delisted")
    for type_, (listings, companies) in sorted(s.by_type.items()):
        noun = "funds" if type_ == "etf" else "companies"
        print(f"  {type_:<8}{listings:>8} listings{companies:>8} {noun}")
    print(f"FinanceDatabase commit {s.source_commit}")
    print(f"refreshed {s.refreshed_at:%Y-%m-%d %H:%M} UTC")
