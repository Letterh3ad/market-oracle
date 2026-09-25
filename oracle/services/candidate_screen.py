"""From a company name to a listing that may be suggested or added, decided in code: the one
company the name means (a match by the start of its name only when its business fits the news,
a brand only when the news or the question picks one of its group, a subsidiary as the parent
the news names), the never list on every listing, the user's removals, what is already watched,
and a live line with recent prices. Discovery runs and questions both screen this way; a model
is asked only what the rules cannot settle."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from oracle.market.bars import is_stale
from oracle.market.sources import NoData, SourceFailed
from oracle.market.yahoo import Quote, search_quotes
from oracle.research import name_check
from oracle.research.llm import ChatModel, ModelError
from oracle.research.mentions import same_company
from oracle.services.brands import (
    behind_quotes, is_place, named_whole, names_in, other_words, parents, sized, sounds_private, the_group,
    usable, written_before,
)
from oracle.services.backfill_service import ADDED_FROM_YAHOO, add_from_yahoo
from oracle.services.business_fit import BusinessFit, NamedIn
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.http import open_client
from oracle.services.never_list import is_never, never_company, never_names
from oracle.services.prices_service import update_listing
from oracle.services.universe_service import live_siblings, search_universe
from oracle.store.discoveries import DISCOVERED_LIST, DiscoveryRepo
from oracle.store.health import HealthRepo
from oracle.store.universe import ListingProfile, SearchHit, UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.universe.listings import MARKET_CAP_BANDS, blind_name, is_receipt
from oracle.universe.regions import home_country

NEVER = "on the never list"
REMOVED = "you removed it before"
PRIVATE = "private, not listed"
NOT_FOUND = "not in the universe under that name"
# Shorter names ("US", "GE") start many companies' names: said so when none can be picked.
MIN_LEADING_NAME = 4
# Deep enough that a short brand's companies ("LG") are not crowded out by symbols starting the same.
SEARCH_DEPTH = 40
# A one-word name this long may be a group's name cut short ("STMicro" for STMicroelectronics).
MIN_CUT_NAME = 6
_BIG = frozenset({"Mega Cap", "Large Cap"})
_TINY = frozenset({"Micro Cap", "Nano Cap", None})
_CAP_INDEX = {band: i for i, band in enumerate(MARKET_CAP_BANDS)}
# Adjacent bands can nearly touch (a small Large Cap barely tops a big Mid Cap), but
# FinanceDatabase's own band floors are roughly 5x to 20x apart, so a two-band gap is
# always at least 5x: enough to pick the bigger company instead of staying ambiguous.
CLEARLY_BIGGER_BANDS = 2
# Yahoo ranks a brand's own listing this far above the rest when it knows the brand ("Google").
CLEAR_LEAD = 10
# Yahoo searches per pass: a tick or an answer names a few dozen companies, each could want one.
MAX_LOOKUPS = 5
MAX_NAMED_CANDIDATES = 4
_DOTCOM = re.compile(r"\.com\b", re.IGNORECASE)
EXACT, START, SYMBOL, LOOKUP, PARENT = "exact", "start", "symbol", "lookup", "parent"


class Screen:
    """One pass over candidates. Each company is looked at once; `max_checks` bounds the price
    fetches. `model` settles names the rules cannot; without one they are refused."""

    def __init__(self, ctx: AppContext, max_checks: int, model: ChatModel | None = None) -> None:
        self._ctx = ctx
        self._max_checks = max_checks
        self._model = model
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
        self._hits: dict[str, tuple[list[SearchHit], dict[str, set[str]]]] = {}
        self._settled: dict[tuple[str, NamedIn | None], _Match] = {}
        self._quotes: dict[str, list[Quote] | None] = {}
        self._lookups = 0
        self._picks: dict[tuple[str, NamedIn], SearchHit | None] = {}
        self._owners: dict[tuple[str, NamedIn], _Match | None] = {}
        self._fit = BusinessFit(self._universe)
        self.notes: dict[str, str] = {}
        """Name -> how it was matched when not by its own name ("Intrinsic is part of Alphabet Inc.")."""

    @property
    def checks_left(self) -> bool:
        return self.checks < self._max_checks

    def hurry(self) -> None:
        """Out of time: names are matched by the rules alone from here, with no search or model."""
        self._model = None
        self._lookups = MAX_LOOKUPS

    async def identify(self, name: str, symbol: str | None = None,
                       named_in: NamedIn | None = None) -> tuple[ListingProfile | None, str | None]:
        """The company's own shares the name (or `symbol`, when known) means, else why not.
        A name matched only by its start is refused when what it was `named_in` shows another
        business. (None, None) for a company already looked at in this pass."""
        if is_never(name, self._never):
            return None, NEVER
        profile, why_not = (self._universe.profile(symbol), None) if symbol else await self._matched(name, named_in)
        if profile is None:
            return None, why_not or NOT_FOUND
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

    async def resolve(self, name: str, named_in: NamedIn | None = None) -> ListingProfile | None:
        """The company's own shares the name means, with no other rule applied and nothing
        marked as looked at; None for a fund or a depositary receipt."""
        profile = (await self._matched(name, named_in))[0]
        return profile if profile and profile.type == "equity" and not is_receipt(profile.name) else None

    async def _matched(self, name: str, named_in: NamedIn | None) -> tuple[ListingProfile | None, str | None]:
        """The company the name means, or why none. Exact names and symbols are taken as they
        are; a name matched by its start must fit what it was named in."""
        if (name, named_in) not in self._settled:
            self._settled[name, named_in] = await self._settle(name, named_in)
        found = self._settled[name, named_in]
        if found.note:
            self.notes[name] = found.note
        else:
            self.notes.pop(name, None)
        return found.profile, found.why_not

    async def _settle(self, name: str, named_in: NamedIn | None) -> _Match:
        found = await self._find(name, named_in or NamedIn(), parent=named_in is not None)
        p = found.profile
        if p is None or named_in is None:
            return found
        why = None
        if found.kind == START:
            # "Tesla's Optimus" is no small company whose name happens to start so.
            small = p.market_cap not in _BIG
            if small and (owner := await self._parent(name, named_in)):
                return owner
            why = ((small and self._written_after(p, name, named_in)) or self._fit.misfit(p, name, named_in)
                   or self._unsure(p, name, named_in))
        elif found.kind == SYMBOL and p.market_cap in _TINY and not names_in(p.name, named_in.items):
            # "CATL" is a tiny SPAC's ticker as well as a battery maker's brand.
            why = f"{name} as a symbol is {p.name}, which the news does not name"
        if why is None:
            return found
        # A refused match may still be a brand of a parent the news names, or listed under the name.
        return (await self._parent(name, named_in) or await self._looked_up(name, blind_name(name), p.company_key)
                or _Match(None, why))

    async def _find(self, name: str, named_in: NamedIn, parent: bool, lookup: bool = True) -> _Match:
        """One of its listings named exactly that (legal forms aside, biggest first as the search
        ranks them); else the one company whose name starts with it (or, several doing so, the one
        the news or the question picks); else a listing with that symbol exactly as written (never
        a guess from case, so "On" or "Open" never mean ON or OPEN); else the model's pick of those
        whose name starts with it. With none of those: the parent the news names, else what Yahoo
        lists under the name."""
        want = blind_name(_DOTCOM.sub("", name))
        hits, names = self._search(want)
        if exact := [h for h in hits if want in names[h.symbol]]:
            return _Match(self._universe.profile(exact[0].symbol), kind=EXACT)
        leading = [h for h in hits if any(same_company(n, want) for n in names[h.symbol])]
        if not leading and len(want) >= MIN_CUT_NAME and " " not in want:
            leading = [h for h in hits if any(n.split()[0].startswith(want) for n in names[h.symbol])]
        leading = behind_quotes(want, sized(leading), hits)
        if leading and (picked := self._picked(want, leading, named_in)):
            return _Match(self._universe.profile(picked.symbol), kind=START)
        symbol_as_written = name.strip()
        if symbol_as_written.isupper() and (by_symbol := self._universe.profile(symbol_as_written)) is not None:
            return _Match(by_symbol, kind=SYMBOL)
        if leading:
            if picked := await self._model_pick(name, want, leading, named_in):
                return _Match(self._universe.profile(picked.symbol), kind=START)
            return _Match(None, _ambiguous(want, leading))
        if parent and (owner := await self._parent(name, named_in)):
            return owner
        looked = await self._looked_up(name, want) if lookup else None
        if looked:
            return looked
        return _Match(None, PRIVATE if sounds_private(name, named_in.items) else NOT_FOUND)

    def _search(self, want: str) -> tuple[list[SearchHit], dict[str, set[str]]]:
        """The usable hits for a name, each with every name its company's listings carry."""
        if want not in self._hits:
            hits = usable(search_universe(self._ctx, want, filters={"type": ["equity"]}, limit=SEARCH_DEPTH))
            names = {h.symbol: {blind_name(n) for x in (h, *self._universe.company_listings(h.company_key))
                                for n in (x.name, _DOTCOM.sub("", x.name))} for h in hits}
            self._hits[want] = hits, names
        return self._hits[want]

    def _picked(self, want: str, leading: list[SearchHit], named_in: NamedIn) -> SearchHit | None:
        """The one company of those starting with the name that the rules pick: the only one; the
        one whose whole name the news writes ("Samsung SDI"); the only one in the question's
        sectors; the group's own listing ("Siemens Aktiengesellschaft"); else the clearly biggest,
        by one band when the question's sectors narrowed them."""
        if len(leading) == 1:
            return leading[0]
        if len(whole := named_whole(want, leading, named_in.items)) == 1:
            return whole[0]
        sectors = self._fit.sectors_asked(named_in.asked)
        narrowed = [h for h in leading if h.sector in sectors]
        if len(narrowed) == 1:
            return narrowed[0]
        pool = narrowed or leading
        if len(group := the_group(want, pool)) == 1:
            return group[0]
        return _clearly_biggest(pool, 1 if narrowed else CLEARLY_BIGGER_BANDS)

    async def _model_pick(self, name: str, want: str, candidates: Sequence[SearchHit],
                          named_in: NamedIn) -> SearchHit | None:
        """The candidate the model says the items mean, asked once per name and news. Never for a
        place ("American"), nor past `name_check.MAX_COMPANIES` candidates (which few would be shown
        is itself a guess)."""
        if (want, named_in) in self._picks:
            return self._picks[want, named_in]
        if (self._model is None or not named_in.items or is_place(want)
                or len(candidates) > name_check.MAX_COMPANIES):
            return None
        try:
            reply = await self._model.reply(name_check.prompt(name, named_in.asked, list(named_in.items),
                                                              [_described(h) for h in candidates]),
                                            name_check.SCHEMA)
        except ModelError:
            # A model that is down stays down for this pass; waiting out its timeout per name helps no one.
            self._model = None
            return None
        at = name_check.picked(reply, len(candidates))
        self._picks[want, named_in] = None if at is None else candidates[at]
        return self._picks[want, named_in]

    def _written_after(self, p: ListingProfile, name: str, named_in: NamedIn) -> str | None:
        """Why a small company is not the name the news writes straight after another company's
        ("Tesla Optimus", "Alphabet Intrinsic"): a product or unit of that one."""
        for owner in written_before(name, named_in.items):
            if is_never(owner, self._never):
                return NEVER
            want = blind_name(owner)
            hits, names = self._search(want)
            if any(want in names[h.symbol] for h in hits):
                return f"not matched to {p.name}: the news writes it after {owner}"
        return None

    def _unsure(self, p: ListingProfile, name: str, named_in: NamedIn) -> str | None:
        """Why a small company matched by the start of its name may be another business of the
        same start ("Lovable" is not Lovable Lingerie): the rest of its name says a trade the news
        does not, and the news never writes its whole name. Rules only: a 4B model confirmed such
        matches about at random."""
        if p.market_cap in _BIG or names_in(p.name, named_in.items):
            return None
        if not self._fit.unsaid(other_words(p.name, name), named_in):
            return None
        if sounds_private(name, named_in.items):
            return f"{PRIVATE} (the news calls {name} a startup; {p.name} is another company)"
        return (f"not matched to {p.name} ({p.industry or p.sector or 'trade unknown'}, "
                f"{p.country or 'country unknown'}): only the start of its name fits")

    async def _parent(self, name: str, named_in: NamedIn) -> _Match | None:
        """The listed company the items say owns the name, once per name and news: the universe
        asked for the first two owners the items give and their shortest forms, Yahoo only for
        those shortest ("Alphabet", never "Why Alphabet")."""
        if (name, named_in) not in self._owners:
            owners = parents(name, named_in.items)
            shortest = [o for o in owners if not any(o.endswith(f" {x}") for x in owners)][:2]
            self._owners[name, named_in] = None
            for owner, lookup in [*((o, False) for o in dict.fromkeys(shortest + owners[:2])),
                                  *((o, True) for o in shortest)]:
                found = await self._find(owner, named_in, parent=False, lookup=lookup)
                if found.profile is not None:
                    self._owners[name, named_in] = _Match(found.profile, kind=PARENT,
                                                          note=f"{name} is part of {found.profile.name}")
                    break
        return self._owners[name, named_in]

    async def _looked_up(self, name: str, want: str, besides: str | None = None) -> _Match | None:
        """What Yahoo's search lists under the name: a company of the universe named so, trading
        under the name as its ticker, or ranked far first when big (a brand, "Google" for Alphabet);
        else a listing the universe lacks, else private. None when the search cannot say, or
        (`besides` a company already found) when it finds no other."""
        quotes = await self._quotes_for(name)
        if quotes is None:
            return None
        equities = [q for q in quotes if q.kind == "EQUITY"]
        lead = quotes[0] if equities and quotes[0] is equities[0] and (
            len(quotes) == 1 or quotes[0].score >= CLEAR_LEAD * quotes[1].score) else None

        def own(q: Quote) -> bool:
            return want in {blind_name(q.name), blind_name(q.short_name)}

        def ticker(q: Quote) -> bool:
            return q.symbol.split(".")[0].upper() == want.replace(" ", "").upper()

        def leads(q: Quote) -> bool:
            return any(same_company(blind_name(n), want) for n in (q.name, q.short_name))

        for rule, big_only in ((own, False), (ticker, False), (lambda q: q is lead, True)):
            for q in equities:
                p = self._universe.profile(q.symbol) if rule(q) else None
                # Past a refused match, a tiny ticker twin is no better a guess than the ticker was.
                if p is None or p.company_key == besides or (big_only and p.market_cap not in _BIG) or (
                        besides and rule is ticker and p.market_cap in _TINY):
                    continue
                return _Match(p, kind=LOOKUP, note=None if same_company(blind_name(p.name), want)
                              else f"{name} is listed as {p.name}")
        if besides is not None:
            return None
        # Yahoo keeps private companies as ".PVT" lines ("Anthropic").
        if any(q.symbol.upper().endswith(".PVT") and (own(q) or leads(q)) for q in quotes):
            return _Match(None, PRIVATE)
        named = next((q for q in equities if own(q) or leads(q)), None)
        listed = lead or named or next((q for q in equities if ticker(q)), None)
        if listed is None:
            return _Match(None, PRIVATE)
        tracked = self._universe.profile(listed.symbol) is not None
        if tracked and named is None:
            # The rules above passed it over (a small line Yahoo merely ranks first).
            return _Match(None, f"listed as {listed.name} ({listed.symbol}), not taken on Yahoo's ranking alone")
        # A line Yahoo only ranks first, not by the name, joins only when Yahoo sizes it big.
        added = named and await self._added(name, named) or (
            lead and lead is not named and not tracked and await self._added(name, lead, _BIG))
        shown = named if tracked else listed
        return added or _Match(None, f"listed as {shown.name} ({shown.symbol}), not in the universe")

    async def _added(self, name: str, q: Quote, bands: frozenset[str] | None = None) -> _Match | None:
        """Yahoo's line added to the universe under the index backfill's guards (only in `bands`
        when given), on one of the pass's lookups; None when none is left or the line may not join."""
        if self._lookups >= MAX_LOOKUPS or is_never(q.name, self._never):
            return None
        self._lookups += 1
        symbol, _ = await add_from_yahoo(self._ctx, q.symbol, bands)
        p = self._universe.profile(symbol) if symbol else None
        return p and _Match(p, kind=LOOKUP, note=f"{name} is listed as {p.name} ({symbol}), {ADDED_FROM_YAHOO}")

    async def _quotes_for(self, name: str) -> list[Quote] | None:
        if name not in self._quotes:
            if self._lookups >= MAX_LOOKUPS:
                return None
            self._lookups += 1
            try:
                async with open_client(self._ctx) as client:
                    self._quotes[name] = await search_quotes(client, name)
            except (NoData, SourceFailed):
                self._quotes[name] = None
        return self._quotes[name]

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

    async def priced_line(self, profile: ListingProfile, line: str | None = None) -> tuple[str | None, str | None]:
        """The company's live line (or `line`) once its recent prices are fetched, else why not."""
        listing = line or self.live_line(profile)
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

    def live_line(self, profile: ListingProfile) -> str | None:
        """The primary line (home or most liquid), else its best live sibling."""
        primary = profile.primary_symbol
        if not HealthRepo(self._ctx.conn).get(primary).dead:
            return primary
        siblings = live_siblings(self._ctx, primary)
        return siblings[0].symbol if siblings else None

    def is_home_line(self, profile: ListingProfile, symbol: str) -> bool:
        """`symbol` is a live line of the company on a main board at home, in the home currency."""
        line = next((x for x in self._universe.company_listings(profile.company_key)
                     if x.symbol.upper() == symbol.upper()), None)
        return line is not None and home_country(line) is not None and not HealthRepo(self._ctx.conn).get(
            line.symbol).dead

    def _keys(self, symbols) -> set[str]:
        return {r.company_key for s in symbols if (r := self._universe.resolve(s))}


@dataclass(frozen=True)
class _Match:
    profile: ListingProfile | None
    why_not: str | None = None
    kind: str = EXACT
    """How it was found: EXACT, START (by the start of its name), SYMBOL, LOOKUP or PARENT."""
    note: str | None = None
    """How, in words, when not by its own name."""


def _ambiguous(want: str, candidates: Sequence[SearchHit]) -> str:
    named = ", ".join(h.name for h in candidates[:MAX_NAMED_CANDIDATES])
    more = f" and {len(candidates) - MAX_NAMED_CANDIDATES} more" if len(candidates) > MAX_NAMED_CANDIDATES else ""
    why = "too short a name to tell which company" if len(want) < MIN_LEADING_NAME else "ambiguous name"
    return f"{why}: {named}{more}"


def _described(h: SearchHit) -> str:
    return (f"{h.name} ({h.country or 'country unknown'}; {h.industry or h.sector or 'trade unknown'}; "
            f"{h.market_cap or 'size unknown'})")


def _clearly_biggest(hits: Sequence[SearchHit], bands: int) -> SearchHit | None:
    """The hit whose company is at least `bands` market-cap bands over the next biggest of the
    group, or None to stay ambiguous (either band unknown, or the two are close enough that which
    is really bigger cannot be told from bands alone)."""
    ranked = sorted(hits, key=lambda h: _CAP_INDEX.get(h.market_cap, len(MARKET_CAP_BANDS)))
    best, next_best = ranked[0], ranked[1]
    if best.market_cap is None or next_best.market_cap is None:
        return None
    if _CAP_INDEX[next_best.market_cap] - _CAP_INDEX[best.market_cap] < bands:
        return None
    return best
