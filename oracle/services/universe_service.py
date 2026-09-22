from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from oracle.jobs import JobBusy, JobLock
from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.services.context import AppContext
from oracle.services.errors import (
    JobAlreadyRunning,
    UniverseNotLoaded,
    UniverseUnavailable,
    UnknownFilterValue,
)
from oracle.store.db import to_utc_iso, transaction
from oracle.store.meta import MetaRepo
from oracle.store.universe import Facet, SearchHit, UniverseRepo
from oracle.universe import financedatabase
from oracle.universe.listings import MARKET_CAP_BANDS, group_companies
from oracle.universe.query import FILTER_FIELDS, SORTS, UniverseQuery, fts_match

__all__ = [
    "FILTER_FIELDS", "SORTS", "Facet", "SearchHit", "UniverseStatus",
    "facets", "refresh_universe", "search_universe", "universe_status",
]

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
    text: str = "",
    *,
    filters: Mapping[str, Sequence[str]] | None = None,
    sort: str | None = None,
    descending: bool = False,
    all_listings: bool = False,
    include_delisted: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[SearchHit]:
    """Companies matching every word of `text` (as a prefix of a name or symbol word)
    and every filter; repeated values of one filter mean either. No text lists the
    whole filtered universe, biggest first."""
    _require_loaded(ctx)
    if sort is not None and sort not in SORTS:
        raise ValueError(f"unknown sort {sort!r}")
    if text.strip() and fts_match(text) is None:
        return []
    repo = UniverseRepo(ctx.conn)
    checked = {name: _check(repo, name, values) for name, values in (filters or {}).items() if values}
    return repo.search(UniverseQuery(
        text=text, filters=checked, sort=sort, descending=descending, all_listings=all_listings,
        include_delisted=include_delisted, limit=limit, offset=offset,
    ))


def facets(ctx: AppContext, name: str) -> list[Facet]:
    _require_loaded(ctx)
    _check_field(name)
    return UniverseRepo(ctx.conn).facets(name)


def _require_loaded(ctx: AppContext) -> None:
    if MetaRepo(ctx.conn).get(_REFRESHED) is None:
        raise UniverseNotLoaded()


def _check_field(name: str) -> None:
    if name not in FILTER_FIELDS:
        raise UnknownFilterValue(f"cannot filter by {name!r}; use one of {', '.join(FILTER_FIELDS)}")


def _check(repo: UniverseRepo, name: str, values: Sequence[str]) -> tuple[str, ...]:
    """The values as stored, or UnknownFilterValue naming the closest real ones."""
    _check_field(name)
    if name == "market_cap":
        # "mega", "Mega" and "Mega Cap" all mean the band.
        known = {_band_key(band): band for band in MARKET_CAP_BANDS}
        key = _band_key
    else:
        known = {v.casefold(): v for v in repo.values(name)}
        key = str.casefold
    out = []
    for value in values:
        k = key(value.strip())
        if k in known:
            out.append(known[k])
            continue
        close = [known[c] for c in difflib.get_close_matches(k, known, n=3, cutoff=0.6)]
        hint = f"did you mean {' or '.join(close)}?" if close else f"see `oracle universe facets {name}`"
        raise UnknownFilterValue(f"no {name.replace('_', ' ')} {value!r}; {hint}")
    return tuple(out)


def _band_key(value: str) -> str:
    return value.casefold().removesuffix(" cap").strip()


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
