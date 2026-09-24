from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from oracle.cli import (
    analyse, automation, backtest, company, discover, fx, gui, jobs, links, models, net, paper, peers, prices, replay,
    research, schedule, status, tag, tick, universe, watch,
)
from oracle.services.context import load_settings, open_context
from oracle.services.errors import ConfigInvalid, ServiceError

SUBCOMMANDS: list[tuple[str, Callable]] = [
    ("status", status.register),
    ("net", net.register),
    ("jobs", jobs.register),
    ("universe", universe.register),
    ("tag", tag.register),
    ("watch", watch.register),
    ("prices", prices.register),
    ("fx", fx.register),
    ("company", company.register),
    ("analyse", analyse.register),
    ("peers", peers.register),
    ("research", research.register),
    ("models", models.register),
    ("discover", discover.register),
    ("links", links.register),
    ("paper", paper.register),
    ("backtest", backtest.register),
    ("replay", replay.register),
    ("tick", tick.register),
    ("schedule", schedule.register),
    ("automation", automation.register),
    ("gui", gui.register),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oracle", description="Market Oracle")
    parser.add_argument("--config", type=Path, default=None, help="path to oracle.toml")
    subparsers = parser.add_subparsers(dest="command")
    for name, register in SUBCOMMANDS:
        register(subparsers.add_parser(name))
    return parser


def main(argv: list[str] | None = None) -> int:
    # Names and headlines come from every market; a console or pipe in a legacy code page
    # (cp1252 on Windows) must show "?" for what it cannot encode, not crash mid-report.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.command:
        parser.print_usage()
        return 2
    try:
        config, source = load_settings(args.config)
    except ConfigInvalid as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    try:
        with open_context(config, source) as ctx:
            return int(args.handler(ctx, args) or 0)
    except ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
