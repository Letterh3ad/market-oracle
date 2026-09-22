from __future__ import annotations

import argparse
from collections.abc import Callable


def add_status_command(
    parser: argparse.ArgumentParser, dest: str, handler: Callable, help: str
) -> None:
    sub = parser.add_subparsers(dest=dest, required=True)
    sub.add_parser("status", help=help).set_defaults(handler=handler)
