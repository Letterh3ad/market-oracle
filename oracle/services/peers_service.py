from __future__ import annotations

from dataclasses import dataclass

from oracle.market.fundamentals import Fundamentals
from oracle.market.sources import Instrument
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.fundamentals_service import PEER_MAX_AGE, fetch_fundamentals
from oracle.services.universe_service import resolve_symbol
from oracle.store.universe import UniverseRepo

__all__ = ["Peer", "PeerGroup", "peer_group"]

# An industry group with fewer peers than this is too thin to compare against.
MIN_PEERS = 5


@dataclass(frozen=True)
class Peer:
    symbol: str
    name: str
    market_cap_band: str | None
    fundamentals: Fundamentals | None
    note: str | None
    """Why fundamentals are missing or old."""


@dataclass(frozen=True)
class PeerGroup:
    symbol: str
    """The company's primary listing."""
    basis: str
    """What the peers share, in words."""
    fell_back: bool
    """True when the industry group was too thin and the sector was used."""
    peers: tuple[Peer, ...]


async def peer_group(ctx: AppContext, symbol: str) -> PeerGroup:
    """The company's comparison group with each peer's fundamentals, fetched when older
    than a week."""
    repo = UniverseRepo(ctx.conn)
    profile = repo.profile(resolve_symbol(ctx, symbol).symbol)
    if profile.type != "equity":
        raise InvalidQuery(f"{profile.symbol} is a fund; peers are for companies")
    limit = ctx.config.analysts.peers
    rows, basis, fell_back = [], "", False
    if profile.industry_group:
        rows = repo.peers(profile.company_key, "industry_group", profile.industry_group, limit)
        basis = f"industry group {profile.industry_group}"
    if len(rows) < MIN_PEERS and profile.sector:
        fell_back = bool(profile.industry_group)
        rows = repo.peers(profile.company_key, "sector", profile.sector, limit)
        basis = f"sector {profile.sector}" + (f" ({profile.industry_group} has too few)" if fell_back else "")
    if not rows:
        raise InvalidQuery(f"{profile.symbol} has no sector in the universe, so no peers")
    found = await fetch_fundamentals(ctx, [Instrument(r.symbol, r.currency) for r in rows], PEER_MAX_AGE)
    peers = tuple(Peer(r.symbol, r.name, r.market_cap, *found[r.symbol]) for r in rows)
    return PeerGroup(profile.primary_symbol, basis, fell_back, peers)
