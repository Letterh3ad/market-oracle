from __future__ import annotations

import argparse
import textwrap
from collections.abc import Callable, Sequence
from datetime import date

from oracle.services.analysis_service import AnalystSignal
from oracle.services.paper_service import FillOutcome, Line, Side, Status


def add_status_command(
    parser: argparse.ArgumentParser, dest: str, handler: Callable, help: str
) -> None:
    sub = parser.add_subparsers(dest=dest, required=True)
    sub.add_parser("status", help=help).set_defaults(handler=handler)


def iso_day(text: str) -> date:
    """An argparse type for a YYYY-MM-DD day."""
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date like 2026-09-18: {text!r}") from exc


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
        what = {Status.FILLED: "bought" if o.order.side == Side.BUY else "sold", Status.REJECTED: "REJECTED",
                Status.PENDING: "waiting"}[o.status]
        print(f"{o.order.symbol}: {what}" + (f" ({o.note})" if o.note else ""))


def print_lines(lines: Sequence[Line], base: str) -> None:
    """One row per line: return, largest fall, trades and costs."""
    costs = f"costs ({base})"
    print(f"{'line':<14}{'return':>9}{'largest fall':>14}{'trades':>8}{costs:>13}")
    for line in lines:
        gain = f"{line.return_:+.2%}" if line.return_ is not None else "none yet"
        print(f"{line.name:<14}{gain:>9}{line.max_fall:>14.1%}{line.trades:>8}{line.costs:>13,.2f}")
