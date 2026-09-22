from __future__ import annotations

import argparse

from oracle.cli.common import add_status_command
from oracle.services.context import AppContext
from oracle.services.jobs_service import job_statuses


def register(parser: argparse.ArgumentParser) -> None:
    add_status_command(parser, "jobs_command", status, "who holds each job lock, and whether it is stale")


def status(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = job_statuses(ctx)
    if not rows:
        print("no jobs held")
        return 0
    print(f"{'job':<14}{'pid':<10}{'host':<16}{'age':<8}{'beat':<8}state")
    for s in rows:
        state = f"STALE ({s.stale_reason})" if s.stale_reason else "held"
        print(
            f"{s.job:<14}{s.pid:<10}{s.host[:15]:<16}{s.age_s:<8.0f}{s.heartbeat_age_s:<8.0f}{state}"
        )
    return 0
