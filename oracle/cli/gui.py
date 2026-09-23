from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI

from oracle.gui.app import build_app
from oracle.services.context import AppContext


def register(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    host, port = ctx.config.gui.host, ctx.config.gui.port
    print(f"Market Oracle GUI on http://{host}:{port} (Ctrl+C to stop)")
    serve(build_app(ctx), host, port)
    return 0


def serve(app: FastAPI, host: str, port: int) -> None:
    # In this thread, so the context's SQLite connection stays on the thread that opened it.
    uvicorn.run(app, host=host, port=port, log_level="warning")
