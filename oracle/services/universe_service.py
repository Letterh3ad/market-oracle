from __future__ import annotations

from dataclasses import dataclass
import re
from datetime import datetime

from oracle.jobs import JobBusy, JobLock
from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.services.context import AppContext
from oracle.services.errors import JobAlreadyRunning, UniverseNotLoaded, UniverseUnavailable
from oracle.store.db import to_utc_iso, transaction
from oracle.store.meta import MetaRepo
from oracle.store.universe import SearchHit, UniverseRepo
from oracle.universe import financedatabase
from oracle.universe.listings import group_companies

JOB = "universe_refresh"
# Far longer than a refresh takes; a crashed holder is freed by the pid check instead.
LEASE_S = 1800.0
USER_AGENT = "market-oracle/0.1 (personal research tool)"
_COMMIT = "universe_commit"
_REFRESHED = "universe_refreshed_at"


@dataclass(frozen=True)
class UniverseStatus:
    loaded: bool
    listings: int
    companies: int
    delisted: int
    source_commit: str | None
    refreshed_at: datetime | None


def universe_status(ctx: AppContext) -> UniverseStatus:
    meta = MetaRepo(ctx.conn)
    counts = UniverseRepo(ctx.conn).counts()
    refreshed = meta.get(_REFRESHED)
    return UniverseStatus(
        loaded=refreshed is not None,
        listings=counts.listings,
        companies=counts.companies,
        delisted=counts.delisted,
        source_commit=meta.get(_COMMIT),
        refreshed_at=datetime.fromisoformat(refreshed) if refreshed else None,
    )


def search_universe(
    ctx: AppContext,
    query: str,
    *,
    all_listings: bool = False,
    include_delisted: bool = False,
    limit: int = 20,
) -> list[SearchHit]:
    """Companies whose name or symbol words start with every word of `query`, best
    first. An exact symbol match leads and is shown as that listing."""
    if MetaRepo(ctx.conn).get(_REFRESHED) is None:
        raise UniverseNotLoaded()
    # Only word characters reach FTS5, each as a quoted prefix term, so no user input
    # can form FTS syntax.
    words = re.findall(r"\w+", query)
    if not words:
        return []
    match = " ".join(f'"{w}"*' for w in words)
    return UniverseRepo(ctx.conn).search(
        match, query, all_listings=all_listings, include_delisted=include_delisted, limit=limit
    )


async def refresh_universe(ctx: AppContext) -> UniverseStatus:
    """Download FinanceDatabase and swap it in whole: a failure anywhere leaves the
    previous universe untouched."""
    lock = JobLock(ctx.conn, ctx.clock)
    try:
        handle = lock.acquire(JOB, LEASE_S)
    except JobBusy as exc:
        raise JobAlreadyRunning(str(exc)) from exc
    try:
        sha, listings = await _download(ctx)
        grouped = group_companies(listings)
        if not lock.heartbeat(handle):
            raise JobAlreadyRunning(f"{JOB} was taken over by another process; not saving")
        with transaction(ctx.conn):
            UniverseRepo(ctx.conn).replace_all(grouped)
            meta = MetaRepo(ctx.conn)
            meta.set(_COMMIT, sha)
            meta.set(_REFRESHED, to_utc_iso(ctx.clock.utcnow()))
    finally:
        lock.release(handle)
    return universe_status(ctx)


async def _download(ctx: AppContext):
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    client = RateLimitedClient(gate, ctx.clock, USER_AGENT, transport=ctx.transport)
    try:
        sha = await financedatabase.latest_commit(client)
        data = await financedatabase.download(client, sha, "equities.bz2")
        return sha, financedatabase.parse_equities(data)
    except financedatabase.Unavailable as exc:
        raise UniverseUnavailable(str(exc)) from exc
    finally:
        await client.aclose()
