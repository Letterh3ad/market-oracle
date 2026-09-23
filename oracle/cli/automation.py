from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.settings_service import set_automation


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "the master switch for work that starts by itself (scheduled ticks)"
    parser.add_argument("state", nargs="?", choices=("on", "off"), help="turn automation on or off")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.state:
        path = set_automation(ctx, args.state == "on")
        print(f"saved to {path}; a running `oracle gui` picks it up within a minute")
        if args.state == "off":
            print("work already running carries on: stop it from the Activity tab if needed")
    if ctx.config.automation.enabled:
        print("automation on: scheduled work runs when due")
    else:
        print("automation off: nothing starts by itself; buttons and commands still work")
    return 0
