from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from oracle.broker.rules import Lean
from oracle.market.bars import is_stale
from oracle.services.analysis_service import StoredSignal, latest_lean, latest_signals
from oracle.services.context import AppContext
from oracle.services.errors import InvalidSwap, InvalidWatchlistName, UnknownWatchlist
from oracle.services.prices_service import day_change, price_history_in_base
from oracle.services.suggest import closest, hint
from oracle.services.universe_service import Sibling, live_siblings, resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.health import HealthRepo
from oracle.store.paper import PaperRepo
from oracle.store.universe import UniverseRepo
from oracle.store.watchlists import WatchItem, WatchlistRepo, WatchlistSummary

__all__ = [
    "DEFAULT_LIST", "Sibling", "Swapped", "WatchLine", "Watched", "WatchlistSummary",
    "create_watchlist", "show_watchlist", "swap", "unwatch", "watch", "watchlist_symbols", "watchlists",
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
class Swapped:
    old: str
    new: str
    watchlists: tuple[str, ...]
    held_by: tuple[str, ...]
    """Paper accounts still holding the old symbol: positions are never moved."""


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
    """Each analyst's latest stored signal; empty until it is analysed."""
    lean: Lean | None = None
    """The latest lean; None until it is analysed."""
    lean_stale: bool = False
    """Older than the last stored bar: the strategy will not trade on it."""
    dead_since: date | None = None
    """Set when no price source has had data for the listing lately."""
    siblings: tuple[Sibling, ...] = ()
    """A dead listing's live alternatives, best first."""


def watch(ctx: AppContext, symbol: str, watchlist: str = DEFAULT_LIST) -> Watched:
    """Watch one listing (not the company's others: a listing is what gets priced)."""
    name = _name(watchlist)
    listing = resolve_symbol(ctx, symbol)
    stored, added = WatchlistRepo(ctx.conn).add(name, listing.symbol, to_utc_iso(ctx.clock.utcnow()))
    return Watched(listing.symbol, listing.name, stored, added)


def swap(ctx: AppContext, old: str, new: str) -> Swapped:
    """Watch `new` instead of `old` on every list holding `old`, where `new` is another
    live listing of the same company."""
    was, to = resolve_symbol(ctx, old), resolve_symbol(ctx, new)
    if was.symbol == to.symbol:
        raise InvalidSwap(f"{to.symbol} is the same listing")
    members = {x.symbol: x for x in UniverseRepo(ctx.conn).company_listings(was.company_key)}
    if to.symbol not in members:
        raise InvalidSwap(f"{to.symbol} is not a listing of {was.name}")
    if members[to.symbol].delisted:
        raise InvalidSwap(f"{to.symbol} is delisted")
    if (health := HealthRepo(ctx.conn).get(to.symbol)).dead:
        raise InvalidSwap(f"no price source has had data for {to.symbol} since {health.since}")
    lists = WatchlistRepo(ctx.conn).swap(was.symbol, to.symbol)
    if not lists:
        raise InvalidSwap(f"{was.symbol} is on no watchlist")
    return Swapped(was.symbol, to.symbol, lists, PaperRepo(ctx.conn).holders(was.symbol))


def create_watchlist(ctx: AppContext, watchlist: str) -> tuple[str, bool]:
    """An empty list, or the one already so named (without case): (its name, whether new)."""
    return WatchlistRepo(ctx.conn).create(_name(watchlist), to_utc_iso(ctx.clock.utcnow()))


def unwatch(ctx: AppContext, symbol: str, watchlist: str = DEFAULT_LIST) -> bool:
    """False when it was not on the list. Works on symbols gone from the universe."""
    return WatchlistRepo(ctx.conn).remove(_name(watchlist), symbol.strip())


def show_watchlist(ctx: AppContext, watchlist: str = DEFAULT_LIST) -> list[WatchLine]:
    name = _name(watchlist)
    repo = WatchlistRepo(ctx.conn)
    if repo.name_of(name) is None and name.casefold() == DEFAULT_LIST:
        return []
    return [_with_price(ctx, item) for item in repo.items(_existing(repo, name))]


def watchlist_symbols(ctx: AppContext, watchlist: str) -> tuple[str, list[str]]:
    """(the list's stored name, its symbols in the order added)."""
    repo = WatchlistRepo(ctx.conn)
    name = _existing(repo, _name(watchlist))
    return name, [item.symbol for item in repo.items(name)]


def _existing(repo: WatchlistRepo, name: str) -> str:
    stored = repo.name_of(name)
    if stored is None:
        known = {s.name.casefold(): s.name for s in repo.summaries()}
        near = [known[k] for k in closest(name.casefold(), known)]
        raise UnknownWatchlist(f"no watchlist {name!r}; " + hint(near, "see the list of watchlists"))
    return stored


def watchlists(ctx: AppContext) -> list[WatchlistSummary]:
    return WatchlistRepo(ctx.conn).summaries()


def _with_price(ctx: AppContext, item: WatchItem) -> WatchLine:
    if item.missing:
        return WatchLine(**asdict(item))
    since = HealthRepo(ctx.conn).get(item.symbol).since
    dead = {"dead_since": since, "siblings": tuple(live_siblings(ctx, item.symbol)) if since else ()}
    recent = price_history_in_base(ctx, item.symbol, days=2)
    if not recent:
        return WatchLine(**asdict(item), **dead)
    last = recent[-1]
    lean, lean_stale = latest_lean(ctx, item.symbol)
    return WatchLine(
        **asdict(item), **dead,
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
