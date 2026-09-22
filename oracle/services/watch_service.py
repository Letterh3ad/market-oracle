from __future__ import annotations

import difflib
from dataclasses import dataclass

from oracle.services.context import AppContext
from oracle.services.errors import InvalidWatchlistName, UnknownWatchlist
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.watchlists import WatchItem, WatchlistRepo, WatchlistSummary

__all__ = [
    "DEFAULT_LIST", "WatchItem", "Watched", "WatchlistSummary",
    "show_watchlist", "unwatch", "watch", "watchlists",
]

DEFAULT_LIST = "main"
MAX_NAME_LENGTH = 40


@dataclass(frozen=True)
class Watched:
    symbol: str
    name: str
    watchlist: str
    added: bool
    """False when it was already on the list."""


def watch(ctx: AppContext, symbol: str, watchlist: str = DEFAULT_LIST) -> Watched:
    """Watch one listing (not the company's others: a listing is what gets priced)."""
    name = _name(watchlist)
    listing = resolve_symbol(ctx, symbol)
    stored, added = WatchlistRepo(ctx.conn).add(name, listing.symbol, to_utc_iso(ctx.clock.utcnow()))
    return Watched(listing.symbol, listing.name, stored, added)


def unwatch(ctx: AppContext, symbol: str, watchlist: str = DEFAULT_LIST) -> bool:
    """False when it was not on the list. Works on symbols gone from the universe."""
    return WatchlistRepo(ctx.conn).remove(_name(watchlist), symbol.strip())


def show_watchlist(ctx: AppContext, watchlist: str = DEFAULT_LIST) -> list[WatchItem]:
    name = _name(watchlist)
    repo = WatchlistRepo(ctx.conn)
    if repo.name_of(name) is None:
        if name.casefold() == DEFAULT_LIST:
            return []
        known = {s.name.casefold(): s.name for s in repo.summaries()}
        close = [known[k] for k in difflib.get_close_matches(name.casefold(), known, n=3, cutoff=0.6)]
        hint = f"; did you mean {' or '.join(close)}?" if close else "; see `oracle watch lists`"
        raise UnknownWatchlist(f"no watchlist {name!r}{hint}")
    return repo.items(name)


def watchlists(ctx: AppContext) -> list[WatchlistSummary]:
    return WatchlistRepo(ctx.conn).summaries()


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise InvalidWatchlistName("a watchlist name needs at least one visible character")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidWatchlistName(f"a watchlist name is at most {MAX_NAME_LENGTH} characters")
    return name
