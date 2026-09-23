from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from oracle.cli import analyse, company, fx, gui, jobs, net, paper, peers, prices, status, tag, universe, watch
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
    ("paper", paper.register),
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
