from __future__ import annotations

import argparse
from collections.abc import Callable


def add_status_command(
    parser: argparse.ArgumentParser, dest: str, handler: Callable, help: str
) -> None:
    sub = parser.add_subparsers(dest=dest, required=True)
    sub.add_parser("status", help=help).set_defaults(handler=handler)


def cut(text: str | None, width: int) -> str:
    """Fit a column: truncated with a trailing ~."""
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "~"
