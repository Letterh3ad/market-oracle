from __future__ import annotations

import argparse
import socket
import sys
import time
from collections.abc import Callable

import httpx
import uvicorn
from fastapi import FastAPI

from oracle.gui.app import build_app
from oracle.services.context import AppContext

BIND_WAIT_S = 30.0
"""How long a restarted server waits for the old one to let go of the port."""


class PortBusy(Exception):
    pass


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--restart", action="store_true", help="ask the running GUI server to restart")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    host, port = ctx.config.gui.host, ctx.config.gui.port
    if args.restart:
        return restart(f"http://{host}:{port}")
    print(f"Market Oracle GUI on http://{host}:{port} (Ctrl+C to stop)")
    try:
        serve(build_app(ctx, schedule=True), host, port)
    except PortBusy as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def restart(url: str) -> int:
    try:
        answer = httpx.post(f"{url}/api/app/restart", timeout=10.0)
    except httpx.TransportError:
        print(f"no Market Oracle GUI is running on {url}", file=sys.stderr)
        return 1
    body = answer.json()
    if answer.status_code != 202:
        print(f"restart refused: {body.get('error')}", file=sys.stderr)
        return 1
    cut = body["cut_off"]
    print("Restarting" + (f"; cut off: {', '.join(cut)}" if cut else "") + f". Back on {url} shortly.")
    return 0


def bind(host: str, port: int, wait_s: float = BIND_WAIT_S, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], float] = time.monotonic) -> socket.socket:
    """A listening socket on the port, retried while another process (an old server
    finishing its shutdown) still holds it."""
    deadline = now() + wait_s
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
            sock.listen(128)
            return sock
        except OSError:
            sock.close()
            if now() >= deadline:
                raise PortBusy(f"port {port} on {host} is still in use after {wait_s:.0f}s: "
                               "is another Market Oracle running?") from None
            sleep(0.5)


def serve(app: FastAPI, host: str, port: int) -> None:
    # In this thread, so the context's SQLite connection stays on the thread that opened it.
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))

    def stop() -> None:
        server.should_exit = True

    app.state.control.on_exit = stop
    server.run(sockets=[bind(host, port)])
