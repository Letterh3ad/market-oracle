from __future__ import annotations

import argparse
import textwrap
from collections.abc import Callable

from oracle.services.analysis_service import AnalystSignal
from oracle.services.paper_service import FillOutcome


def add_status_command(
    parser: argparse.ArgumentParser, dest: str, handler: Callable, help: str
) -> None:
    sub = parser.add_subparsers(dest=dest, required=True)
    sub.add_parser("status", help=help).set_defaults(handler=handler)


def cut(text: str | None, width: int) -> str:
    """Fit a column: truncated with a trailing ~."""
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "~"


def print_signal(s: AnalystSignal, indent: int, note: str = "") -> None:
    """One analyst's signal: a summary line, then its reason wrapped beneath."""
    print(f"{' ' * indent}{s.analyst:<12}{s.direction.value:<9}score {s.score:+.2f}  confidence {s.confidence:.0%}"
          f"  {s.as_of.isoformat()}{note}")
    print(textwrap.fill(s.reason, 88, initial_indent=" " * (indent + 2), subsequent_indent=" " * (indent + 2)))


def print_fills(outcomes: list[FillOutcome]) -> None:
    for o in outcomes:
        what = {"filled": "bought" if o.order.side == "buy" else "sold", "rejected": "REJECTED",
                "pending": "waiting"}[o.status]
        print(f"{o.order.symbol}: {what}" + (f" ({o.note})" if o.note else ""))
