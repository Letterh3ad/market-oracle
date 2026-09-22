from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.universe_service import (
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
    search = sub.add_parser("search", help="find companies by name or symbol")
    search.add_argument("query")
    search.add_argument("--all-listings", action="store_true", help="every exchange listing, not one per company")
    search.add_argument("--include-delisted", action="store_true")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(handler=search_cmd)


def refresh(ctx: AppContext, args: argparse.Namespace) -> int:
    print("downloading FinanceDatabase...", flush=True)
    _print(asyncio.run(refresh_universe(ctx)))
    return 0


def status(ctx: AppContext, args: argparse.Namespace) -> int:
    _print(universe_status(ctx))
    return 0


def search_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    hits = search_universe(
        ctx, args.query, all_listings=args.all_listings,
        include_delisted=args.include_delisted, limit=args.limit,
    )
    if not hits:
        print("no matches")
        return 0
    print(f"{'symbol':<12}{'name':<32}{'country':<16}{'exch':<6}{'sector':<24}size")
    for h in hits:
        notes = [n for n in (
            _others(h.listings - 1) if h.listings > 1 and not args.all_listings else "",
            "delisted" if h.delisted else "",
        ) if n]
        size = (h.market_cap or "?").removesuffix(" Cap")
        print(
            f"{h.symbol:<12}{_cut(h.name, 31):<32}{_cut(h.country, 15):<16}{h.exchange or '':<6}"
            f"{_cut(h.sector, 23):<24}{size:<7}{'  '.join(notes)}".rstrip()
        )
    return 0


def _others(n: int) -> str:
    return f"+{n} listing" + ("s" if n > 1 else "")


def _cut(text: str | None, width: int) -> str:
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "~"


def _print(s: UniverseStatus) -> None:
    if not s.loaded:
        print("universe not loaded yet: run `oracle universe refresh`")
        return
    print(f"{s.listings} listings, {s.companies} companies, {s.delisted} delisted")
    print(f"FinanceDatabase commit {s.source_commit}")
    print(f"refreshed {s.refreshed_at:%Y-%m-%d %H:%M} UTC")
