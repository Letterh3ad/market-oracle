from __future__ import annotations

import argparse
from datetime import datetime, timezone

from oracle.cli.common import add_status_command
from oracle.services.context import AppContext
from oracle.services.net_service import source_statuses


def register(parser: argparse.ArgumentParser) -> None:
    add_status_command(parser, "net_command", status, "per-source rate, recent spend and breaker state")


def status(ctx: AppContext, args: argparse.Namespace) -> int:
    print(f"{'source':<14}{'rate/s':<8}{'last_1h':<9}state")
    for s in source_statuses(ctx):
        if s.state == "paused":
            until = datetime.fromtimestamp(s.open_until, timezone.utc).strftime("%H:%M:%S UTC")
            state = f"OPEN until {until} ({s.reason})"
        elif s.state == "waiting":
            state = f"next slot in {s.next_slot_in_s:.0f}s"
        else:
            state = "ok"
        print(f"{s.source:<14}{s.rate_per_second:<8g}{s.requests_last_hour:<9}{state}")
    return 0
