from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.status_service import status


def register(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    report = status(ctx)
    source = str(report.config_source) if report.config_source else "defaults (no oracle.toml found)"
    rows = [
        ("schema", report.schema_version),
        ("database", report.db_path),
        ("config", source),
        ("currency", report.base_currency),
        ("created", report.created_at),
        ("version", report.version),
    ]
    print("market-oracle")
    for name, value in rows:
        print(f"  {name:<10}{value}")
    return 0
