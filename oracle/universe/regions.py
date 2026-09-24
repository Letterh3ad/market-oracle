"""Places: the regions people group countries in, the words for them, each country's currency,
and the line a company trades on at home (a main board in the country's own currency, so a New
York ADR, a Berlin line or a London line quoted in dollars never stands in)."""

from __future__ import annotations

from collections.abc import Iterable, Set

from oracle.market.bars import major_unit
from oracle.universe.exchanges import EXCHANGES, exchange_country
from oracle.universe.listings import Listing, rank_listings

MAIN_BOARD_TIER = 2
_EURO = ("Austria", "Belgium", "Cyprus", "Estonia", "Finland", "France", "Germany", "Greece", "Ireland", "Italy",
         "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Portugal", "Spain")
CURRENCY: dict[str, str] = {
    **dict.fromkeys(_EURO, "EUR"),
    "United States": "USD", "United Kingdom": "GBP", "Switzerland": "CHF", "Sweden": "SEK", "Denmark": "DKK",
    "Norway": "NOK", "Iceland": "ISK", "Czech Republic": "CZK", "Hungary": "HUF", "Poland": "PLN", "Turkey": "TRY",
    "Russia": "RUB", "Israel": "ILS", "Saudi Arabia": "SAR", "Qatar": "QAR", "Egypt": "EGP", "South Africa": "ZAR",
    "Canada": "CAD", "Mexico": "MXN", "Brazil": "BRL", "Argentina": "ARS", "Chile": "CLP", "Venezuela": "VES",
    "Japan": "JPY", "China": "CNY", "Hong Kong": "HKD", "Taiwan": "TWD", "South Korea": "KRW", "India": "INR",
    "Singapore": "SGD", "Malaysia": "MYR", "Thailand": "THB", "Indonesia": "IDR", "Australia": "AUD",
    "New Zealand": "NZD", "Sri Lanka": "LKR",
}
"""Each country's own currency: the one its home lines trade in."""

REGIONS: dict[str, frozenset[str]] = {
    "Europe": frozenset({*_EURO, "Czech Republic", "Denmark", "Guernsey", "Hungary", "Iceland", "Isle of Man",
                         "Jersey", "Norway", "Poland", "Sweden", "Switzerland", "United Kingdom"}),
    "Nordics": frozenset({"Denmark", "Finland", "Iceland", "Norway", "Sweden"}),
    "Scandinavia": frozenset({"Denmark", "Norway", "Sweden"}),
    "Benelux": frozenset({"Belgium", "Netherlands", "Luxembourg"}),
    "DACH": frozenset({"Germany", "Austria", "Switzerland"}),
    "North America": frozenset({"United States", "Canada"}),
    "Latin America": frozenset({"Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru"}),
    "Asia": frozenset({"Japan", "China", "Hong Kong", "Taiwan", "South Korea", "Singapore", "India", "Indonesia",
                       "Malaysia", "Thailand", "Philippines", "Vietnam"}),
    "Middle East": frozenset({"Israel", "Saudi Arabia", "Qatar", "United Arab Emirates"}),
    "Oceania": frozenset({"Australia", "New Zealand"}),
}
# How people and news name a place. Bare "UK" and "US" are not nationalities: "wins UK subsidy".
NATIONALITIES: dict[str, tuple[str, ...]] = {
    "Europe": ("european", "europe's"), "United States": ("american", "america's", "us-based"),
    "United Kingdom": ("british", "britain's", "uk-based", "uk's"), "Germany": ("german",),
    "France": ("french",), "Netherlands": ("dutch",), "Switzerland": ("swiss",), "Sweden": ("swedish",),
    "Finland": ("finnish",), "Norway": ("norwegian",), "Denmark": ("danish",), "Italy": ("italian",),
    "Spain": ("spanish",), "Ireland": ("irish",), "Belgium": ("belgian",), "Austria": ("austrian",),
    "Poland": ("polish",), "Japan": ("japanese",), "China": ("chinese",), "South Korea": ("korean", "korea's"),
    "Taiwan": ("taiwanese",), "India": ("indian",), "Israel": ("israeli",), "Canada": ("canadian",),
    "Australia": ("australian",), "Brazil": ("brazilian",), "Nordics": ("nordic",),
    "Scandinavia": ("scandinavian",), "Asia": ("asian",), "Latin America": ("latin american",),
}
_OTHER_NAMES = {"us": "United States", "usa": "United States", "america": "United States",
                "uk": "United Kingdom", "britain": "United Kingdom", "great britain": "United Kingdom",
                "korea": "South Korea", "holland": "Netherlands", "nordic countries": "Nordics",
                "the americas": "North America", "latam": "Latin America", "asia pacific": "Asia"}
_PLACES = {p.casefold(): p for p in (*CURRENCY, *REGIONS, *(c for r in REGIONS.values() for c in r))}
_PLACE_OF = {form: place for place, forms in NATIONALITIES.items() for form in forms}


def place_of(word: str) -> str | None:
    """The country or region a nationality word means ("dutch", "europe's", "uk-based")."""
    return _PLACE_OF.get(word.casefold())


def countries_of(text: str) -> frozenset[str] | None:
    """The countries a region, country or nationality names ("Europe", "the Nordics", "German",
    "UK"); None for anything else."""
    key = " ".join(text.casefold().split()).removeprefix("the ")
    place = _PLACES.get(key) or _OTHER_NAMES.get(key) or _PLACE_OF.get(key)
    if place is None:
        return None
    return REGIONS.get(place, frozenset({place}))


def home_country(line: Listing) -> str | None:
    """The country whose main board the line trades on in that country's currency, or None."""
    tier = EXCHANGES.get(line.exchange or "", (None, 9))[1]
    country = exchange_country(line.exchange)
    if line.delisted or tier > MAIN_BOARD_TIER or not line.currency or country is None:
        return None
    return country if CURRENCY.get(country) == major_unit(line.currency)[0] else None


def home_line(listings: Iterable[Listing], countries: Set[str] | None, dead: Set[str] = frozenset()) -> Listing | None:
    """The company's best-ranked live home line in one of `countries` (anywhere when None)."""
    dead = {s.upper() for s in dead}
    return next((x for x in rank_listings(list(listings), dead) if x.symbol.upper() not in dead
                 and (country := home_country(x)) is not None and (countries is None or country in countries)),
                None)
