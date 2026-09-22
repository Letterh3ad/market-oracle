from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.universe_service import UniverseStatus, refresh_universe, universe_status


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="universe_command", required=True)
    sub.add_parser(
        "refresh", help="download FinanceDatabase (~17MB) and replace the local universe"
    ).set_defaults(handler=refresh)
    sub.add_parser("status", help="what is loaded and when it was refreshed").set_defaults(handler=status)


def refresh(ctx: AppContext, args: argparse.Namespace) -> int:
    print("downloading FinanceDatabase...", flush=True)
    _print(asyncio.run(refresh_universe(ctx)))
    return 0


def status(ctx: AppContext, args: argparse.Namespace) -> int:
    _print(universe_status(ctx))
    return 0


def _print(s: UniverseStatus) -> None:
    if not s.loaded:
        print("universe not loaded yet: run `oracle universe refresh`")
        return
    print(f"{s.listings} listings, {s.companies} companies, {s.delisted} delisted")
    print(f"FinanceDatabase commit {s.source_commit}")
    print(f"refreshed {s.refreshed_at:%Y-%m-%d %H:%M} UTC")
