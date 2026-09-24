"""The universe screened for a question's industry, region and size: the listed companies that
fit, largest first, each on its home line, whether or not a headline names them (headlines
favour famous names and startups)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from oracle.services.context import AppContext
from oracle.services.industry_words import Classes, classes
from oracle.services.never_list import is_never, never_names
from oracle.store.health import HealthRepo
from oracle.store.universe import ListingProfile, UniverseRepo
from oracle.universe.exchanges import EXCHANGES
from oracle.universe.listings import Listing, is_receipt, is_receipt_symbol
from oracle.universe.regions import countries_of, home_country, home_line

# The universe often calls a foreign company's US OTC line (and so the company) American.
# Nouns a phrase may end in that name no trade of their own.
_TRADE_NOUNS = frozenset({"maker", "makers", "manufacturer", "manufacturers", "contractor", "contractors",
                          "company", "companies", "firm", "firms", "producer", "producers", "supplier",
                          "suppliers", "major", "majors", "player", "players", "stock", "stocks", "group",
                          "groups", "provider", "providers", "operator", "operators", "developer", "developers"})
_UNTRUSTED = tuple(code for code, (country, tier) in EXCHANGES.items() if country == "United States" and tier == 3)


@dataclass(frozen=True)
class Filters:
    industries: tuple[Classes, ...] = ()
    places: tuple[str, ...] = ()
    """The regions and countries as the plan wrote them."""
    countries: frozenset[str] | None = None
    """None for anywhere."""
    market_caps: tuple[str, ...] = ()
    unplaced: bool = False
    """Regions were asked for and none was recognised: screening anywhere would widen the question."""

    @property
    def usable(self) -> bool:
        """A region or a size alone would list any big company there, whatever the question."""
        return bool(self.industries) and not self.unplaced

    def describe(self) -> str:
        caps = ", ".join(c.lower() + "s" for c in self.market_caps)
        return "; ".join(filter(None, (", ".join(c.text for c in self.industries), ", ".join(self.places), caps)))


@dataclass(frozen=True)
class Screened:
    profile: ListingProfile
    line: str
    """Its home line: a main board in its home currency, in the region asked when one was."""
    evidence: str
    """What the universe says fits, e.g. "universe: Semiconductors & Semiconductor Equipment,
    Netherlands, Mega Cap"."""


@dataclass(frozen=True)
class Screening:
    companies: tuple[Screened, ...] = field(default=())
    """Every company that fits, largest first."""

    @property
    def matched(self) -> int:
        return len(self.companies)


def plan_filters(ctx: AppContext, industries: list[str], regions: list[str],
                 market_caps: tuple[str, ...]) -> tuple[Filters, list[str]]:
    """The plan's words as the universe's classifications and countries, with a note for each
    word that names nothing the universe holds (it is dropped)."""
    known = UniverseRepo(ctx.conn).classifications()
    matched, places, countries, notes = [], [], set(), []
    for word in industries:
        if found := industry(word, known):
            matched.append(found)
        else:
            notes.append(f'"{word}" was not recognised as a sector or industry, so the universe was not screened '
                         "for it")
    for word in regions:
        if (found := countries_of(word)) is not None:
            places.append(word)
            countries |= found
        else:
            notes.append(f'"{word}" was not recognised as a country or region, so the universe was not screened '
                         "for it")
    unplaced = bool(regions) and not places
    if unplaced and matched:
        notes.append("no region asked for was recognised, so the universe was not screened")
    return Filters(tuple(matched), tuple(places), frozenset(countries) if places else None, market_caps,
                   unplaced), notes


def industry(phrase: str, known: dict[str, frozenset[str]]) -> Classes | None:
    """The classifications a plan's phrase names: the whole phrase, else the phrase without a
    trade noun at its end ("defence contractors", "oil majors", "EV makers"). Never one word of
    it alone: "electric vehicles" is not Electric Utilities, "private equity" not Equity REITs."""
    words = phrase.split()
    trimmed = words[:-1] if len(words) > 1 and words[-1].casefold() in _TRADE_NOUNS else None
    found = classes(phrase, known) or (trimmed and classes(" ".join(trimmed), known))
    return replace(found, text=phrase) if found else None


def screen_universe(ctx: AppContext, filters: Filters) -> Screening:
    """The companies that fit, largest band first, then the most widely listed: equity, not on
    the never list, not a depositary receipt, with a live home line in the countries asked, or
    with none asked, in the company's own country (never an ADR, a receipt or a foreign
    secondary line; only a US OTC primary, whose country the universe gets wrong, may be anywhere)."""
    if not filters.usable:
        return Screening()
    universe = UniverseRepo(ctx.conn)
    found = universe.screen([(c.level, c.values) for c in filters.industries], filters.countries,
                            filters.market_caps, _UNTRUSTED)
    listings = universe.listings_of(p.company_key for p in found)
    dead = HealthRepo(ctx.conn).dead_symbols()
    never = never_names(ctx, user=True)
    out = []
    for p in found:
        lines = listings[p.company_key]
        if _receipt(p) or any(is_never(x.name, never) for x in (p, *lines)):
            continue
        countries = filters.countries
        if countries is None and p.exchange not in _UNTRUSTED:
            if not p.country:
                continue
            countries = {p.country}
        if (line := home_line([x for x in lines if not _receipt(x)], countries, dead)) is None:
            continue
        out.append(Screened(p, line.symbol, _evidence(p, home_country(line), filters)))
    return Screening(tuple(out))


def _receipt(line: Listing | ListingProfile) -> bool:
    return is_receipt(line.name) or is_receipt_symbol(line.symbol)


def _evidence(p: ListingProfile, line_country: str, filters: Filters) -> str:
    what = next(value for c in filters.industries if (value := c.holds(p)))
    trusted = p.country and p.exchange not in _UNTRUSTED and (filters.countries is None or p.country in filters.countries)
    return f"universe: {what}, {p.country if trusted else line_country}, {p.market_cap or 'size unknown'}"
