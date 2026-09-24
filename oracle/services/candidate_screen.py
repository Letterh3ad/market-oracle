"""From a company name to a listing that may be suggested or added, decided in code: the one
company the name means (a match by the start of its name only when its business fits the news),
the never list on every listing, the user's removals, what is already watched, and a live line
with recent prices. Discovery runs and questions both screen this way."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from oracle.market.bars import is_stale
from oracle.research.mentions import same_company
from oracle.services.business_fit import BusinessFit, NamedIn
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.never_list import is_never, never_company, never_names
from oracle.services.prices_service import update_listing
from oracle.services.universe_service import live_siblings, search_universe
from oracle.store.discoveries import DISCOVERED_LIST, DiscoveryRepo
from oracle.store.health import HealthRepo
from oracle.store.universe import ListingProfile, SearchHit, UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.universe.listings import MARKET_CAP_BANDS, blind_name, is_receipt

NEVER = "on the never list"
REMOVED = "you removed it before"
# Shorter names ("US", "GE") start too many companies' names to be matched by their start.
MIN_LEADING_NAME = 4
_CAP_INDEX = {band: i for i, band in enumerate(MARKET_CAP_BANDS)}
# Adjacent bands can nearly touch (a small Large Cap barely tops a big Mid Cap), but
# FinanceDatabase's own band floors are roughly 5x to 20x apart, so a two-band gap is
# always at least 5x: enough to pick the bigger company instead of staying ambiguous.
CLEARLY_BIGGER_BANDS = 2


class Screen:
    """One pass over candidates. Each company is looked at once; `max_checks` bounds the price
    fetches."""

    def __init__(self, ctx: AppContext, max_checks: int) -> None:
        self._ctx = ctx
        self._max_checks = max_checks
        self.checks = 0
        self._universe = UniverseRepo(ctx.conn)
        self._never = never_names(ctx, user=True)
        self._removed_symbols, self._removed_keys = DiscoveryRepo(ctx.conn).removed()
        lists = WatchlistRepo(ctx.conn)
        self._watched = self._keys(s for w in lists.summaries() if w.name.casefold() != DISCOVERED_LIST
                                   for s in (i.symbol for i in lists.items(w.name)))
        self._listed = {r.company_key: i.symbol for i in lists.items(DISCOVERED_LIST)
                        if (r := self._universe.resolve(i.symbol))}
        self.on_list = len(self._listed)
        self._seen: set[str] = set()
        self._found: dict[str, _Match] = {}
        self._fit = BusinessFit(self._universe)

    @property
    def checks_left(self) -> bool:
        return self.checks < self._max_checks

    def identify(self, name: str, symbol: str | None = None,
                 named_in: NamedIn | None = None) -> tuple[ListingProfile | None, str | None]:
        """The company's own shares the name (or `symbol`, when known) means, else why not.
        A name matched only by its start is refused when what it was `named_in` shows another
        business. (None, None) for a company already looked at in this pass."""
        if is_never(name, self._never):
            return None, NEVER
        profile, why_not = (self._universe.profile(symbol), None) if symbol else self._matched(name, named_in)
        if profile is None:
            return None, why_not or "not in the universe under that name"
        if profile.company_key in self._seen:
            return None, None
        self._seen.add(profile.company_key)
        if profile.type != "equity" or is_receipt(profile.name):
            return None, "not a company's own shares"
        if never_company(self._ctx, profile.symbol, user=True):
            return None, NEVER
        return profile, None

    def profile(self, symbol: str) -> ListingProfile | None:
        return self._universe.profile(symbol)

    def resolve(self, name: str, named_in: NamedIn | None = None) -> ListingProfile | None:
        """The company's own shares the name means, with no other rule applied and nothing
        marked as looked at; None for a fund or a depositary receipt."""
        profile = self._matched(name, named_in)[0]
        return profile if profile and profile.type == "equity" and not is_receipt(profile.name) else None

    def _matched(self, name: str, named_in: NamedIn | None) -> tuple[ListingProfile | None, str | None]:
        """The company the name means, or why none. Exact names and symbols are taken as they
        are; a name matched by its start must fit what it was named in."""
        if name not in self._found:
            self._found[name] = self._lookup(name)
        found = self._found[name]
        if found.profile and found.by_start and named_in and (why := self._fit.misfit(found.profile, named_in)):
            return None, why
        return found.profile, found.why_not

    def watched(self, profile: ListingProfile) -> bool:
        """On one of the user's own lists."""
        return profile.company_key in self._watched

    def listed(self, profile: ListingProfile) -> bool:
        """On the discovered list."""
        return profile.company_key in self._listed

    def listed_as(self, profile: ListingProfile) -> str | None:
        """The discovered list's line of the company, which may not be its primary one."""
        return self._listed.get(profile.company_key)

    def removed(self, profile: ListingProfile) -> bool:
        symbols = {s.upper() for s in self._universe.company_symbols(profile.company_key)}
        return profile.company_key in self._removed_keys or bool(symbols & self._removed_symbols)

    def now_listed(self, profile: ListingProfile, symbol: str) -> None:
        self._listed[profile.company_key] = symbol

    async def priced_line(self, profile: ListingProfile) -> tuple[str | None, str | None]:
        """The company's live line once its recent prices are fetched, else why not."""
        listing = self.live_line(profile)
        if listing is None:
            return None, "no listing with live prices"
        self.checks += 1
        try:
            update = await update_listing(self._ctx, listing)
        except ServiceError as exc:
            return None, f"no price data ({exc})"
        if update.last_date is None or is_stale(update.last_date, self._ctx.clock.utcnow().date()):
            return None, f"its prices are out of date (last {update.last_date})"
        return listing, None

    def _lookup(self, name: str) -> _Match:
        """The company the name means, or why none: one of its listings named exactly that
        (legal forms aside, biggest first as the search ranks them), else the one company
        whose name starts with it, else (when several do) the one clearly biggest by market
        cap, else a listing with that symbol exactly as written (never a guess from case, so
        "On" or "Open" never mean ON or OPEN). A short, shared or too-close-to-call leading
        name is never guessed at."""
        want = blind_name(name)
        hits = search_universe(self._ctx, want, filters={"type": ["equity"]}, limit=10)
        names = {h.symbol: {blind_name(h.name)} | {blind_name(x.name) for x in
                                                   self._universe.company_listings(h.company_key)} for h in hits}
        exact = [h for h in hits if want in names[h.symbol]]
        leading = {h.company_key: h for h in hits if any(same_company(n, want) for n in names[h.symbol])}
        if exact:
            return _Match(self._universe.profile(exact[0].symbol))
        if len(want) >= MIN_LEADING_NAME and len(leading) == 1:
            return _Match(self._universe.profile(next(iter(leading.values())).symbol), by_start=True)
        if len(want) >= MIN_LEADING_NAME and len(leading) > 1 and (biggest := _clearly_biggest(leading.values())):
            return _Match(self._universe.profile(biggest.symbol), by_start=True)
        symbol_as_written = name.strip()
        if symbol_as_written.isupper() and (by_symbol := self._universe.profile(symbol_as_written)) is not None:
            return _Match(by_symbol)
        if not leading:
            return _Match(None)
        return _Match(None, "too short a name to tell which company" if len(want) < MIN_LEADING_NAME
                      else "ambiguous name")

    def live_line(self, profile: ListingProfile) -> str | None:
        """The primary line (home or most liquid), else its best live sibling."""
        primary = profile.primary_symbol
        if not HealthRepo(self._ctx.conn).get(primary).dead:
            return primary
        siblings = live_siblings(self._ctx, primary)
        return siblings[0].symbol if siblings else None

    def _keys(self, symbols) -> set[str]:
        return {r.company_key for s in symbols if (r := self._universe.resolve(s))}


@dataclass(frozen=True)
class _Match:
    profile: ListingProfile | None
    why_not: str | None = None
    by_start: bool = False
    """Found by the start of its name, not the whole of it or its symbol."""


def _clearly_biggest(hits: Iterable[SearchHit]) -> SearchHit | None:
    """The hit whose company is at least `CLEARLY_BIGGER_BANDS` market-cap bands over the
    next biggest of the group, or None to stay ambiguous (either band unknown, or the two
    are close enough that which is really bigger cannot be told from bands alone)."""
    ranked = sorted(hits, key=lambda h: _CAP_INDEX.get(h.market_cap, len(MARKET_CAP_BANDS)))
    best, next_best = ranked[0], ranked[1]
    if best.market_cap is None or next_best.market_cap is None:
        return None
    if _CAP_INDEX[next_best.market_cap] - _CAP_INDEX[best.market_cap] < CLEARLY_BIGGER_BANDS:
        return None
    return best
