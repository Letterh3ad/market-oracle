from __future__ import annotations

import argparse

from oracle.services import holiday_service
from oracle.services.context import AppContext
from oracle.services.holiday_service import Report


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("The holiday watch: arm checks everything first and refuses on any failure, backs the "
                          "database up, starts the replay study and registers the watchdog and morning-report tasks. "
                          "Disarm undoes it.")
    sub = parser.add_subparsers(dest="holiday_command", required=True)
    sub.add_parser("arm", help="preflight, back up, start the study, register the tasks").set_defaults(
        handler=lambda ctx, args: _run(ctx, "arm"))
    sub.add_parser("disarm", help="pause the study and delete both tasks").set_defaults(
        handler=lambda ctx, args: _run(ctx, "disarm"))
    sub.add_parser("status", help="the checks, the tasks, the study and the watch's own status").set_defaults(
        handler=lambda ctx, args: _run(ctx, "status"))


def _run(ctx: AppContext, command: str) -> int:
    report: Report = getattr(holiday_service.real_holiday(), command)(ctx)
    for c in report.checks:
        print(f"  {'ok  ' if c.ok else 'FAIL'}  {c.name}: {c.detail}" + ("" if c.ok else f"\n        fix: {c.fix}"))
    for warning in report.warnings:
        print(f"  WARN  {warning}")
    for line in report.lines:
        print(line)
    return 0 if report.ok else 1
