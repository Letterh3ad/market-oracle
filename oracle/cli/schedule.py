from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.schedule_service import schedule_status
from oracle.services.settings_service import set_schedule


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "automatic ticks while `oracle gui` runs (weekdays, local time)"
    on_off = parser.add_mutually_exclusive_group()
    on_off.add_argument("--on", dest="enabled", action="store_const", const=True, help="turn scheduling on")
    on_off.add_argument("--off", dest="enabled", action="store_const", const=False, help="turn scheduling off")
    parser.add_argument("--time", help='local run time, 24-hour, like "22:30"')
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    current = ctx.config.schedule
    if args.enabled is not None or args.time:
        enabled = current.enabled if args.enabled is None else args.enabled
        path = set_schedule(ctx, enabled, args.time or current.time)
        print(f"saved to {path}; a running `oracle gui` picks it up within a minute")
    view = schedule_status(ctx)
    if view.enabled:
        print(f"on, weekdays at {view.time}; next run {view.next_run.astimezone():%a %Y-%m-%d %H:%M} (while the GUI runs)")
    else:
        print(f"off (would run weekdays at {view.time}): turn on with `oracle schedule --on`")
    return 0
