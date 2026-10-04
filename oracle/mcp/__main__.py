"""`python -m oracle.mcp [--config oracle.toml]`: the literature index's MCP server on stdio."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from oracle.mcp.server import serve
from oracle.services.context import load_settings, open_context
from oracle.services.errors import ConfigInvalid, ServiceError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m oracle.mcp",
                                     description="Market Oracle's literature index as a local MCP server (stdio)")
    parser.add_argument("--config", type=Path, default=None, help="path to oracle.toml")
    args = parser.parse_args(argv)
    try:
        config, source = load_settings(args.config)
        with open_context(config, source) as ctx:
            serve(ctx, sys.stdin.buffer, sys.stdout.buffer)
    except (ConfigInvalid, ServiceError) as exc:
        # stdout carries only JSON-RPC; the client shows the server's stderr.
        print(f"market-oracle MCP server: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
