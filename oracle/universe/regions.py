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
         "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Portugal", "Spain", "Croatia", "Slovakia",
         "Slovenia")
CURRENCY: dict[str, str] = {
    **dict.fromkeys(_EURO, "EUR"),
    "United States": "USD", "United Kingdom": "GBP", "Switzerland": "CHF", "Sweden": "SEK", "Denmark": "DKK",
    "Norway": "NOK", "Iceland": "ISK", "Czech Republic": "CZK", "Hungary": "HUF", "Poland": "PLN", "Turkey": "TRY",
    "Russia": "RUB", "Israel": "ILS", "Saudi Arabia": "SAR", "Qatar": "QAR", "Egypt": "EGP", "South Africa": "ZAR",
    "Canada": "CAD", "Mexico": "MXN", "Brazil": "BRL", "Argentina": "ARS", "Chile": "CLP", "Venezuela": "VES",
    "Japan": "JPY", "China": "CNY", "Hong Kong": "HKD", "Taiwan": "TWD", "South Korea": "KRW", "India": "INR",
    "Singapore": "SGD", "Malaysia": "MYR", "Thailand": "THB", "Indonesia": "IDR", "Australia": "AUD",
    "New Zealand": "NZD", "Sri Lanka": "LKR", "Colombia": "COP", "Peru": "PEN", "Philippines": "PHP",
    "Vietnam": "VND", "United Arab Emirates": "AED", "Romania": "RON", "Bulgaria": "BGN", "Nigeria": "NGN",
    "Kenya": "KES", "Morocco": "MAD", "Kuwait": "KWD",
}
"""Each country's own currency: the one its home lines trade in."""

_EU = frozenset({*_EURO, "Bulgaria", "Czech Republic", "Denmark", "Hungary", "Poland", "Romania", "Sweden"})
_EUROPE = _EU | {"Guernsey", "Iceland", "Isle of Man", "Jersey", "Norway", "Switzerland", "United Kingdom"}
_ASIA = frozenset({"Japan", "China", "Hong Kong", "Taiwan", "South Korea", "Singapore", "India", "Indonesia",
                   "Malaysia", "Thailand", "Philippines", "Vietnam"})
REGIONS: dict[str, frozenset[str]] = {
    "Europe": _EUROPE,
    "EU": _EU,
    "Eurozone": frozenset(_EURO),
    "Western Europe": _EUROPE - {"Bulgaria", "Croatia", "Czech Republic", "Estonia", "Hungary", "Latvia", "Lithuania",
                                 "Poland", "Romania", "Slovakia", "Slovenia"},
    "Nordics": frozenset({"Denmark", "Finland", "Iceland", "Norway", "Sweden"}),
    "Scandinavia": frozenset({"Denmark", "Norway", "Sweden"}),
    "Benelux": frozenset({"Belgium", "Netherlands", "Luxembourg"}),
    "DACH": frozenset({"Germany", "Austria", "Switzerland"}),
    "North America": frozenset({"United States", "Canada"}),
    "Latin America": frozenset({"Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru"}),
    "Asia": _ASIA,
    "Southeast Asia": frozenset({"Singapore", "Malaysia", "Thailand", "Indonesia", "Philippines", "Vietnam"}),
    "Asia-Pacific": _ASIA | {"Australia", "New Zealand"},
    "Middle East": frozenset({"Israel", "Saudi Arabia", "Qatar", "United Arab Emirates", "Kuwait"}),
    "Africa": frozenset({"South Africa", "Egypt", "Nigeria", "Kenya", "Morocco", "Mauritius"}),
    "Oceania": frozenset({"Australia", "New Zealand"}),
    # Roughly MSCI's emerging markets.
    "Emerging markets": frozenset({"China", "India", "Taiwan", "South Korea", "Brazil", "Mexico", "Chile", "Colombia",
                                   "Peru", "South Africa", "Egypt", "Saudi Arabia", "Qatar", "United Arab Emirates",
                                   "Kuwait", "Turkey", "Poland", "Hungary", "Czech Republic", "Greece", "Indonesia",
                                   "Malaysia", "Thailand", "Philippines"}),
}
# How people and news name a place. Bare "UK" and "US" are not nationalities: "wins UK subsidy".
NATIONALITIES: dict[str, tuple[str, ...]] = {
    "Europe": ("european", "europe's"), "United States": ("american", "america's", "us-based"),
    "United Kingdom": ("british", "britain's", "uk-based", "uk's"), "Germany": ("german",),
    "France": ("french",), "Netherlands": ("dutch",), "Switzerland": ("swiss",), "Sweden": ("swedish",),
    "Finland": ("finnish",), "Norway": ("norwegian",), "Denmark": ("danish",), "Italy": ("italian",),
    "Spain": ("spanish",), "Ireland": ("irish",), "Belgium": ("belgian",), "Austria": ("austrian",),
    "Poland": ("polish",), "Japan": ("japanese",), "China": ("chinese",),
    "South Korea": ("korean", "korea's", "south korean"), "Taiwan": ("taiwanese",), "India": ("indian",),
    "Israel": ("israeli",), "Canada": ("canadian",), "Australia": ("australian",), "Brazil": ("brazilian",),
    "Nordics": ("nordic",), "Scandinavia": ("scandinavian",), "Asia": ("asian",),
    "Latin America": ("latin american",), "Singapore": ("singaporean",), "Mexico": ("mexican",),
    "Portugal": ("portuguese",), "Greece": ("greek",), "Turkey": ("turkish",), "Indonesia": ("indonesian",),
    "Malaysia": ("malaysian",), "Thailand": ("thai",), "Vietnam": ("vietnamese",), "Philippines": ("filipino",),
    "Saudi Arabia": ("saudi",), "South Africa": ("south african",), "Argentina": ("argentine", "argentinian"),
    "Chile": ("chilean",), "Czech Republic": ("czech",), "Hungary": ("hungarian",), "Iceland": ("icelandic",),
    "Africa": ("african",), "Southeast Asia": ("southeast asian",),
}
_OTHER_NAMES = {"us": "United States", "usa": "United States", "america": "United States",
                "united states of america": "United States", "uk": "United Kingdom", "britain": "United Kingdom",
                "great britain": "United Kingdom", "england": "United Kingdom", "scotland": "United Kingdom",
                "wales": "United Kingdom", "english": "United Kingdom", "scottish": "United Kingdom",
                "korea": "South Korea", "holland": "Netherlands", "nordic countries": "Nordics",
                "americas": "North America", "latam": "Latin America", "apac": "Asia-Pacific",
                "european union": "EU", "euro area": "Eurozone", "euro zone": "Eurozone",
                "south east asia": "Southeast Asia", "emerging market": "Emerging markets",
                "uae": "United Arab Emirates", "emirates": "United Arab Emirates"}
_PLACE_OF = {form: place for place, forms in NATIONALITIES.items() for form in forms}


def _key(text: str) -> str:
    """A place name as looked up: case, dots and hyphens aside ("U.S.", "Asia-Pacific")."""
    words = text.casefold().replace(".", "").replace("-", " ").split()
    return " ".join(words[1:] if words[:1] == ["the"] else words)


_NAMES = {**{_key(p): p for p in (*CURRENCY, *REGIONS, *(c for r in REGIONS.values() for c in r))},
          **_OTHER_NAMES, **{_key(form): place for form, place in _PLACE_OF.items()}}


def place_of(word: str) -> str | None:
    """The country or region a nationality word means ("dutch", "europe's", "uk-based")."""
    return _PLACE_OF.get(word.casefold())


def countries_of(text: str) -> frozenset[str] | None:
    """The countries a region, country or nationality names ("Europe", "the Nordics", "German",
    "UK"); None for anything else."""
    place = _NAMES.get(_key(text))
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
