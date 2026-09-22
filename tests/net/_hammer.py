"""Subprocess body for the cross-process gate test: acquire N slots, log wall times."""

import asyncio
import sys
import time

from oracle.clock import SystemClock
from oracle.config import SourceConfig
from oracle.net.gate import SourceGate
from oracle.store.db import connect


async def main(db: str, rate: float, n: int, start_at: float, out: str) -> None:
    conn = connect(db)
    gate = SourceGate(conn, SystemClock(), {"shared": SourceConfig(rate_per_second=rate)})
    await asyncio.sleep(max(0.0, start_at - time.time()))
    stamps = []
    for _ in range(n):
        await gate.acquire("shared")
        stamps.append(time.time())
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(repr(s) for s in stamps))
    conn.close()


if __name__ == "__main__":
    db, rate, n, start_at, out = sys.argv[1:]
    asyncio.run(main(db, float(rate), int(n), float(start_at), out))
