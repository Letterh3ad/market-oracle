from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from oracle.broker.rules import Lean
from oracle.market.bars import is_stale
from oracle.services.analysis_service import StoredSignal, latest_lean, latest_signals
from oracle.services.context import AppContext
from oracle.services.errors import InvalidWatchlistName, UnknownWatchlist
from oracle.services.prices_service import day_change, price_history_in_base
from oracle.services.suggest import closest, hint
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.watchlists import WatchItem, WatchlistRepo, WatchlistSummary

__all__ = [
    "DEFAULT_LIST", "WatchLine", "Watched", "WatchlistSummary",
    "create_watchlist", "show_watchlist", "unwatch", "watch", "watchlists",
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


@dataclass(frozen=True)
class WatchLine(WatchItem):
    last_date: date | None = None
    last_close: float | None = None
    price_currency: str | None = None
    """The stored bars' currency (pounds for London, not the universe's hint)."""
    close_base: float | None = None
    rate_source: str | None = None
    """Where the rate behind close_base came from, for attribution."""
    day_change: float | None = None
    """Ratio against the previous stored close: 0.05 is up 5%."""
    stale: bool = False
    signals: tuple[StoredSignal, ...] = ()
    """Each analyst's latest stored signal; empty until `oracle analyse`."""
    lean: Lean | None = None
    """The latest lean; None until `oracle analyse`."""
    lean_stale: bool = False
    """Older than the last stored bar: the strategy will not trade on it."""


def watch(ctx: AppContext, symbol: str, watchlist: str = DEFAULT_LIST) -> Watched:
    """Watch one listing (not the company's others: a listing is what gets priced)."""
    name = _name(watchlist)
    listing = resolve_symbol(ctx, symbol)
    stored, added = WatchlistRepo(ctx.conn).add(name, listing.symbol, to_utc_iso(ctx.clock.utcnow()))
    return Watched(listing.symbol, listing.name, stored, added)


def create_watchlist(ctx: AppContext, watchlist: str) -> tuple[str, bool]:
    """An empty list, or the one already so named (without case): (its name, whether new)."""
    return WatchlistRepo(ctx.conn).create(_name(watchlist), to_utc_iso(ctx.clock.utcnow()))


def unwatch(ctx: AppContext, symbol: str, watchlist: str = DEFAULT_LIST) -> bool:
    """False when it was not on the list. Works on symbols gone from the universe."""
    return WatchlistRepo(ctx.conn).remove(_name(watchlist), symbol.strip())


def show_watchlist(ctx: AppContext, watchlist: str = DEFAULT_LIST) -> list[WatchLine]:
    name = _name(watchlist)
    repo = WatchlistRepo(ctx.conn)
    if repo.name_of(name) is None:
        if name.casefold() == DEFAULT_LIST:
            return []
        known = {s.name.casefold(): s.name for s in repo.summaries()}
        near = [known[k] for k in closest(name.casefold(), known)]
        raise UnknownWatchlist(f"no watchlist {name!r}; " + hint(near, "see `oracle watch lists`"))
    return [_with_price(ctx, item) for item in repo.items(name)]


def watchlists(ctx: AppContext) -> list[WatchlistSummary]:
    return WatchlistRepo(ctx.conn).summaries()


def _with_price(ctx: AppContext, item: WatchItem) -> WatchLine:
    if item.missing:
        return WatchLine(**asdict(item))
    recent = price_history_in_base(ctx, item.symbol, days=2)
    if not recent:
        return WatchLine(**asdict(item))
    last = recent[-1]
    lean, lean_stale = latest_lean(ctx, item.symbol)
    return WatchLine(
        **asdict(item),
        last_date=last.bar.date, last_close=last.bar.close, price_currency=last.bar.currency,
        close_base=last.close_base, rate_source=last.rate_source, day_change=day_change(recent),
        stale=is_stale(last.bar.date, ctx.clock.utcnow().date()),
        signals=tuple(latest_signals(ctx, item.symbol)),
        lean=lean, lean_stale=lean_stale,
    )


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise InvalidWatchlistName("a watchlist name needs at least one visible character")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidWatchlistName(f"a watchlist name is at most {MAX_NAME_LENGTH} characters")
    return name
