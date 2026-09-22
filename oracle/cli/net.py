from __future__ import annotations

import argparse
from datetime import datetime, timezone

from oracle.services.context import AppContext
from oracle.services.net_service import source_statuses


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="net_command", required=True)
    sub.add_parser("status", help="per-source rate, recent spend and breaker state").set_defaults(
        handler=status
    )


def status(ctx: AppContext, args: argparse.Namespace) -> int:
    print(f"{'source':<14}{'rate/s':<8}{'last_1h':<9}state")
    for s in source_statuses(ctx):
        if s.open_until is not None:
            until = datetime.fromtimestamp(s.open_until, timezone.utc).strftime("%H:%M:%S UTC")
            state = f"OPEN until {until} ({s.reason})"
        elif s.deferred_s > 0:
            state = f"backing off {s.deferred_s:.0f}s"
        else:
            state = "ok"
        print(f"{s.source:<14}{s.rate_per_second:<8g}{s.requests_last_hour:<9}{state}")
    return 0
