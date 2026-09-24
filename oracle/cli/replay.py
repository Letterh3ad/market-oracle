from __future__ import annotations

import argparse

from oracle.cli import cutoff


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "Research replays at past dates after the model's cutoff, scored against what prices did next."
    sub = parser.add_subparsers(dest="replay_command", required=True)
    cutoff.add_commands(sub)
