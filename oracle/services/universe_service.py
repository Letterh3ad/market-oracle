from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from oracle.net.client import RateLimitedClient
from oracle.services.context import AppContext
from oracle.services.http import open_client
from oracle.services.jobs_service import single_flight
from oracle.services.suggest import closest, hint
from oracle.services.errors import (
    InvalidQuery,
    UniverseNotLoaded,
    UniverseUnavailable,
    UnknownFilterValue,
    UnknownSymbol,
)
from oracle.store.db import to_utc_iso, transaction
from oracle.store.health import HealthRepo
from oracle.store.meta import MetaRepo
from oracle.store.tags import TagRepo
from oracle.store.universe import Facet, Resolved, SearchHit, UniverseRepo
from oracle.universe import financedatabase
from oracle.universe.listings import MARKET_CAP_BANDS, Listing, group_companies, rank_listings
from oracle.universe.tags import normalise_tag
from oracle.universe.query import FILTER_FIELDS, SORTS, UniverseQuery, fts_match

__all__ = [
    "FILTER_FIELDS", "SORTS", "Facet", "Resolved", "SearchHit", "Sibling", "UniverseBrief", "UniverseStatus",
    "facets", "live_siblings", "refresh_universe", "resolve_symbol", "search_universe", "universe_brief",
    "universe_status",
]

JOB = "universe_refresh"
# Far longer than a refresh takes; a crashed holder is freed by the pid check instead.
LEASE_S = 1800.0
MAX_LIMIT = 500
_COMMIT = "universe_commit"
_REFRESHED = "universe_refreshed_at"


@dataclass(frozen=True)
class UniverseStatus:
    loaded: bool
    listings: int
    companies: int
    delisted: int
    by_type: dict[str, tuple[int, int]]
    """type -> (listings, companies)"""
    source_commit: str | None
    refreshed_at: datetime | None


@dataclass(frozen=True)
class UniverseBrief:
    loaded: bool
    listings: int
    refreshed_at: datetime | None


def universe_brief(ctx: AppContext) -> UniverseBrief:
    """What a status line needs, without `universe_status`'s per-company counts (over half
    a second on the full universe)."""
    refreshed = MetaRepo(ctx.conn).get(_REFRESHED)
    return UniverseBrief(refreshed is not None, UniverseRepo(ctx.conn).listing_count(),
                         datetime.fromisoformat(refreshed) if refreshed else None)


def universe_status(ctx: AppContext) -> UniverseStatus:
    meta = MetaRepo(ctx.conn)
    counts = UniverseRepo(ctx.conn).counts()
    refreshed = meta.get(_REFRESHED)
    return UniverseStatus(
        loaded=refreshed is not None,
        listings=counts.listings,
        companies=counts.companies,
        delisted=counts.delisted,
        by_type=counts.by_type,
        source_commit=meta.get(_COMMIT),
        refreshed_at=datetime.fromisoformat(refreshed) if refreshed else None,
    )


def search_universe(
    ctx: AppContext,
    text: str = "",
    *,
    filters: Mapping[str, Sequence[str]] | None = None,
    tags: Sequence[str] = (),
    sort: str | None = None,
    descending: bool = False,
    all_listings: bool = False,
    include_delisted: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[SearchHit]:
    """Companies matching every word of `text` (as a prefix of a name or symbol word)
    and every filter; repeated values of one filter mean either, while every tag must be
    present. No text lists the whole filtered universe, biggest first."""
    _require_loaded(ctx)
    if sort is not None and sort not in SORTS:
        raise InvalidQuery(f"unknown sort {sort!r}")
    if descending and (sort == "relevance" or (sort is None and text.strip())):
        raise InvalidQuery("relevance has one direction; pick another --sort to reverse")
    if text.strip() and fts_match(text) is None:
        return []
    repo = UniverseRepo(ctx.conn)
    hits = repo.search(UniverseQuery(
        text=text,
        filters={n: _canonical_values(repo, n, v) for n, v in (filters or {}).items() if v},
        tags=_canonical_tags(ctx, tags),
        sort=sort, descending=descending, all_listings=all_listings,
        include_delisted=include_delisted, limit=min(limit, MAX_LIMIT), offset=offset,
    ))
    tags_by_company = TagRepo(ctx.conn).of_companies([h.company_key for h in hits])
    return [replace(h, tags=tags_by_company.get(h.company_key, ())) for h in hits]


def facets(ctx: AppContext, name: str) -> list[Facet]:
    _require_loaded(ctx)
    _check_field(name)
    return UniverseRepo(ctx.conn).facets(name)


def resolve_symbol(ctx: AppContext, symbol: str) -> Resolved:
    """The listing as stored, or UnknownSymbol with search suggestions."""
    _require_loaded(ctx)
    found = UniverseRepo(ctx.conn).resolve(symbol)
    if found:
        return found
    near = [h.symbol for h in search_universe(ctx, symbol, limit=3, include_delisted=True)]
    raise UnknownSymbol(f"no listing {symbol!r} in the universe; " + hint(near, "search the universe for it"))


@dataclass(frozen=True)
class Sibling:
    symbol: str
    exchange: str | None
    currency: str | None


def live_siblings(ctx: AppContext, symbol: str) -> list[Sibling]:
    """The company's other listings that are neither delisted nor dead, best first (the
    order the primary is picked in)."""
    listing = resolve_symbol(ctx, symbol)
    dead = HealthRepo(ctx.conn).dead_symbols()
    rows = rank_listings(UniverseRepo(ctx.conn).company_listings(listing.company_key), dead)
    return [Sibling(x.symbol, x.exchange, x.currency) for x in rows
            if x.symbol != listing.symbol and not x.delisted and x.symbol not in dead]


def _require_loaded(ctx: AppContext) -> None:
    if MetaRepo(ctx.conn).get(_REFRESHED) is None:
        raise UniverseNotLoaded()


def _check_field(name: str) -> None:
    if name not in FILTER_FIELDS:
        raise UnknownFilterValue(f"cannot filter by {name!r}; use one of {', '.join(FILTER_FIELDS)}")


def _canonical_values(repo: UniverseRepo, name: str, values: Sequence[str]) -> tuple[str, ...]:
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
        if k not in known:
            near = [known[c] for c in closest(k, known)]
            raise UnknownFilterValue(
                f"no {name.replace('_', ' ')} {value!r}; "
                + hint(near, f"see the {name.replace('_', ' ')} values the universe has")
            )
        out.append(known[k])
    return tuple(out)


def _canonical_tags(ctx: AppContext, tags: Sequence[str]) -> tuple[str, ...]:
    known = set(TagRepo(ctx.conn).all_tags())
    out = []
    for raw in tags:
        try:
            tag = normalise_tag(raw)
        except ValueError:
            tag = raw
        if tag not in known:
            raise UnknownFilterValue(f"no tag {raw!r}; " + hint(closest(tag, known), "see the tag list"))
        out.append(tag)
    return tuple(out)


def _band_key(value: str) -> str:
    return value.casefold().removesuffix(" cap").strip()


async def refresh_universe(ctx: AppContext, on_stage: Callable[[str], None] = lambda _: None) -> UniverseStatus:
    """Download FinanceDatabase and swap it in whole: a failure anywhere leaves the
    previous universe untouched. `on_stage` hears "downloading", then "saving"."""
    with single_flight(ctx, JOB, LEASE_S) as held:
        on_stage("downloading")
        sha, listings = await _download(ctx)
        on_stage("saving")
        grouped = group_companies(listings, HealthRepo(ctx.conn).dead_symbols())
        held.check()
        with transaction(ctx.conn):
            UniverseRepo(ctx.conn).replace_all(grouped)
            meta = MetaRepo(ctx.conn)
            meta.set(_COMMIT, sha)
            meta.set(_REFRESHED, to_utc_iso(ctx.clock.utcnow()))
    return universe_status(ctx)


async def _download(ctx: AppContext) -> tuple[str, list[Listing]]:
    async with open_client(ctx) as client:
        return await _fetch(client)


async def _fetch(client: RateLimitedClient) -> tuple[str, list[Listing]]:
    try:
        sha = await financedatabase.latest_commit(client)
        equities = financedatabase.parse_equities(await financedatabase.download(client, sha, "equities.bz2"))
        etfs = financedatabase.parse_etfs(await financedatabase.download(client, sha, "etfs.bz2"))
        # A few symbols appear in both files (CHAD); the equity row is the fuller one.
        taken = {x.symbol for x in equities}
        return sha, equities + [x for x in etfs if x.symbol not in taken]
    except financedatabase.Unavailable as exc:
        raise UniverseUnavailable(str(exc)) from exc
