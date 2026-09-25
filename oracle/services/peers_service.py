from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from oracle.market.fundamentals import Fundamentals
from oracle.market.sources import Instrument
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.fundamentals_service import PEER_MAX_AGE, fetch_fundamentals
from oracle.services.universe_service import resolve_symbol
from oracle.store.universe import ListingProfile, PeerRow, UniverseRepo
from oracle.universe.corrections import CORRECTIONS, corrected as correct

__all__ = ["Peer", "PeerChoice", "PeerGroup", "choose_peers", "peer_group", "with_fundamentals"]

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


@dataclass(frozen=True)
class PeerChoice:
    """Who the peers are, before any fundamentals are fetched."""

    company: ListingProfile
    basis: str
    fell_back: bool
    rows: tuple[PeerRow, ...]

    def instruments(self) -> list[Instrument]:
        return [Instrument(r.symbol, r.currency) for r in self.rows]


async def peer_group(ctx: AppContext, symbol: str) -> PeerGroup:
    """The company's comparison group with each peer's fundamentals, fetched when older
    than a week."""
    choice = choose_peers(ctx, symbol)
    found = await fetch_fundamentals(ctx, {i: PEER_MAX_AGE for i in choice.instruments()})
    return with_fundamentals(choice, found)


def with_fundamentals(choice: PeerChoice, found: Mapping[str, tuple[Fundamentals | None, str | None]]) -> PeerGroup:
    peers = tuple(Peer(r.symbol, r.name, r.market_cap, *found[r.symbol]) for r in choice.rows)
    return PeerGroup(choice.company.primary_symbol, choice.basis, choice.fell_back, peers)


def choose_peers(ctx: AppContext, symbol: str, corrected: bool = False) -> PeerChoice:
    """`corrected` reads sectors through the corrections table. Off for research and the factor
    analysts (and the pages showing their peers) while the research loop is frozen."""
    fixes = CORRECTIONS if corrected else {}
    repo = UniverseRepo(ctx.conn)
    profile = correct(repo.profile(resolve_symbol(ctx, symbol).symbol), fixes)
    if profile.type != "equity":
        raise InvalidQuery(f"{profile.symbol} is a fund; peers are for companies")
    limit = ctx.config.analysts.peers
    rows, basis, fell_back = [], "", False
    if profile.industry_group:
        rows = repo.peers(profile.company_key, "industry_group", profile.industry_group, limit, fixes)
        basis = f"industry group {profile.industry_group}"
    if len(rows) < MIN_PEERS and profile.sector:
        fell_back = bool(profile.industry_group)
        rows = repo.peers(profile.company_key, "sector", profile.sector, limit, fixes)
        basis = f"sector {profile.sector}" + (f" ({profile.industry_group} has too few)" if fell_back else "")
    if not rows:
        raise InvalidQuery(f"{profile.symbol} has no sector in the universe, so no peers")
    return PeerChoice(profile, basis, fell_back, tuple(rows))
