from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.services.context import AppContext

USER_AGENT = "market-oracle/0.1 (personal research tool)"


@asynccontextmanager
async def open_client(ctx: AppContext) -> AsyncIterator[RateLimitedClient]:
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    client = RateLimitedClient(gate, ctx.clock, USER_AGENT, transport=ctx.transport)
    try:
        yield client
    finally:
        await client.aclose()
