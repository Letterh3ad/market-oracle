"""Subprocess body: a universe refresh that parks mid-download until told to finish."""

import asyncio
import sys
from pathlib import Path

import httpx

from oracle.config import load_config
from oracle.services.context import open_context
from oracle.services.universe_service import refresh_universe
from tests.fakes.financedatabase import BASIC, FakeFinanceDatabase


async def main(cfg: Path, ready: Path, release: Path) -> None:
    fdb = FakeFinanceDatabase(BASIC)

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            ready.write_text("ok")
            while not release.exists():
                await asyncio.sleep(0.05)
        return fdb.handle(request)

    config, source = load_config(cfg)
    with open_context(config, source, transport=httpx.MockTransport(handle)) as ctx:
        await refresh_universe(ctx)


if __name__ == "__main__":
    asyncio.run(main(*map(Path, sys.argv[1:])))
