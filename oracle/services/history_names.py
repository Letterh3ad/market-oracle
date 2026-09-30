"""What the history backfill searches Google News for, per company: the user's own name for it,
else one set by hand here, else its universe name cleaned. A one-word name gets context words,
since Google matches it anywhere in any case ("MAN" is Man Utd and the Isle of Man); a listing
code is not searched at all. Also whether a headline is about the company, by its aliases."""

from __future__ import annotations

import re
from dataclasses import dataclass

from oracle.research.queries import short_name

__all__ = ["DEFAULT_CONTEXT", "SEEDS", "SearchName", "default_search", "is_listing_code", "mentions", "seeded"]

DEFAULT_CONTEXT = "(shares OR stock OR earnings)"
"""Added to a short or common one-word name nobody set by hand, so its news is about the company."""


@dataclass(frozen=True)
class SearchName:
    query: str
    """What goes before the date operators: a quoted phrase, or several joined by OR, with
    context words."""
    aliases: tuple[str, ...]
    """Words a headline about the company uses: the noise measure counts those that name one."""
    skip: bool = False
    """Its news is not searched (a listing code, or a name no query keeps on topic)."""
    source: str = "default"
    """user, seed or default."""
    note: str | None = None


def _seed(query: str, *aliases: str) -> SearchName:
    found = re.findall(r'"([^"]+)"', query)
    return SearchName(query, tuple(dict.fromkeys([*aliases, *found])), source="seed")


# Headline names, set by hand: the main list (51) and map companies whose universe name headlines
# do not use or that are common words. Keyed by primary listing.
SEEDS: dict[str, SearchName] = {
    "BT-A.L": _seed('"BT Group"', "BT"),
    "MSFT": _seed('"Microsoft"', "Bing", "Azure", "Xbox", "LinkedIn"),
    "JPM": _seed('"JPMorgan" OR "JP Morgan"'),
    "JNJ": _seed('"Johnson & Johnson" OR "J&J"'),
    "XOM": _seed('"Exxon"', "ExxonMobil"),
    "AZN.L": _seed('"AstraZeneca"'),
    "ULVR.L": _seed('"Unilever"'),
    "ASML.AS": _seed('"ASML"'),
    "NESN.SW": _seed('"Nestle" OR "Nestlé"'),
    "7203.T": _seed('"Toyota"'),
    "SAP.DE": _seed('"SAP" (software OR shares OR stock OR cloud)'),
    "AAPL": _seed('"Apple"', "iPhone"),
    "NVDA": _seed('"Nvidia"'),
    "AMZN": _seed('"Amazon"'),
    "GOOG": _seed('"Alphabet" OR "Google"', "YouTube", "Waymo"),
    "LLY": _seed('"Eli Lilly"', "Lilly"),
    "PG": _seed('"Procter & Gamble" OR "P&G"', "Procter"),
    "WMT": _seed('"Walmart"'),
    "CAT": _seed('"Caterpillar"'),
    "NEE": _seed('"NextEra"'),
    "LIN": _seed('"Linde"'),
    "AMT": _seed('"American Tower"'),
    "VZ": _seed('"Verizon"'),
    "EXPD": _seed('"Expeditors"'),
    "SHEL.L": _seed('"Shell" (oil OR gas OR LNG OR shares OR energy)'),
    "HSBA.L": _seed('"HSBC"'),
    "0KWZ.L": _seed('"Rio Tinto"'),
    "MC.PA": _seed('"LVMH"'),
    "SIE.DE": _seed('"Siemens"'),
    "NOVO-B.CO": _seed('"Novo Nordisk"'),
    "ALV.DE": _seed('"Allianz"'),
    "IBE.MC": _seed('"Iberdrola"'),
    "RO.SW": _seed('"Roche"'),
    "AIR.PA": _seed('"Airbus"'),
    "VNA.DE": _seed('"Vonovia"'),
    "6758.T": _seed('"Sony"'),
    "8306.T": _seed('"Mitsubishi UFJ" OR "MUFG"'),
    "6861.T": _seed('"Keyence"'),
    "9432.T": _seed('"NTT" (Nippon OR telecom OR Docomo OR shares)', "Docomo"),
    "BHP.AX": _seed('"BHP"'),
    "CBA.AX": _seed('"Commonwealth Bank" OR "CommBank"', "CBA"),
    "CSL.AX": _seed('"CSL" (Behring OR Seqirus OR biotech OR plasma OR shares)', "Behring", "Seqirus", "Vifor"),
    "0700.HK": _seed('"Tencent"'),
    "005930.KS": _seed('"Samsung Electronics"', "Samsung"),
    "D05.SI": _seed('"DBS" (bank OR Singapore OR shares)'),
    "RELIANCE.NS": _seed('"Reliance Industries"', "Reliance"),
    "RY.TO": _seed('"Royal Bank of Canada" OR "RBC"'),
    "SHOP.TO": _seed('"Shopify"'),
    "ENB.TO": _seed('"Enbridge"'),
    "CNR.TO": _seed('"Canadian National Railway" OR "CN Rail"', "Canadian National"),
    "PETR3.SA": _seed('"Petrobras"'),
    # Map companies.
    # MAN SE has been delisted since 2021: this line is effectively Traton, MAN's listed parent.
    "MAN.DE": _seed('"MAN Truck" OR "MAN SE" OR "MAN Energy Solutions" OR "Traton"', "MAN"),
    "IBM": _seed('"IBM"'),
    "BMW.DE": _seed('"BMW"'),
    "ITX.MC": _seed('"Inditex"', "Zara"),
    "BBVA.MC": _seed('"BBVA"'),
    "2222.SR": _seed('"Aramco"'),
    "SPCX": _seed('"SpaceX"'),
    "BA": _seed('"Boeing"'),
    "BP.L": _seed('"BP" (oil OR gas OR shares OR energy)'),
    "MMM": _seed('"3M" (company OR shares OR stock OR earplugs)'),
    "TGT": _seed('"Target" (retailer OR stores OR shares OR stock)'),
    "ORA.PA": _seed('"Orange" (telecom OR operator OR shares)'),
    "META": _seed('"Meta" (Facebook OR Instagram OR Zuckerberg OR shares OR stock)', "Facebook"),
    "FLEX": _seed('"Flex Ltd" OR "Flex" (electronics OR manufacturer)', "Flex"),
    "KRZ.IR": _seed('"Kerry Group"', "Kerry"),
    "VALE3.SA": _seed('"Vale" (mining OR iron OR nickel OR shares)'),
    "FP.PA": _seed('"TotalEnergies"'),
    "DAI.BE": _seed('"Mercedes-Benz"', "Mercedes"),
    "AMD": _seed('"AMD"'),
    "2330.TW": _seed('"TSMC"'),
    "COST": _seed('"Costco"'),
    "UBER": _seed('"Uber"'),
    "CSCO": _seed('"Cisco"'),
    "MU": _seed('"Micron"'),
    "PHIA.AS": _seed('"Philips"'),
    "AD.AS": _seed('"Ahold Delhaize"'),
    "AI.PA": _seed('"Air Liquide"'),
    "601398.SS": _seed('"ICBC"'),
    "EBK.DE": _seed('"EnBW"'),
    "WAB": _seed('"Wabtec"'),
    "LULU": _seed('"Lululemon"'),
    "BIP": _seed('"Brookfield Infrastructure"'),
}


def seeded(symbol: str) -> SearchName | None:
    return SEEDS.get(symbol.upper())


# Share-class and listing words, and long legal forms short_name keeps, that no headline uses.
_LISTING_WORDS = re.compile(r"[\s,]+(?:class [a-z]\b|common stock|ordinary shares|sponsored adr|adr|ads|gdr|"
                            r"aktiengesellschaft|societe europeenne)\b.*$", re.IGNORECASE)
_LEFTOVERS = re.compile(r"(?:[\s,]+(?:&|and|company|companies|p\.l\.c\.?|plc|\(the\)))+\s*$", re.IGNORECASE)
# One-word company names that are also everyday words or places, found on the live maps.
_COMMON_WORDS = frozenset({
    "oracle", "target", "orange", "flex", "vale", "shell", "total", "puma", "corning", "travelers", "prudential",
    "ball", "gap", "next", "sky", "mosaic", "apple", "amazon", "meta", "kerry", "swatch", "ventas", "chewy",
})
SHORT_NAME = 4
"""A one-word name this short (MAN, SAP, DBS, NIO) is an acronym or a word in many headlines."""
# First words that name no company on their own, so a headline using only them is not counted as
# naming it ("Taiwan", "Home" of Home Depot, "Public" of Public Storage).
_GENERIC = frozenset({
    "advanced", "american", "asian", "atlas", "bank", "banco", "british", "canadian", "central", "check", "china",
    "chinese", "church", "compagnie", "crown", "delivery", "deutsche", "digital", "east", "eastern", "first",
    "general", "global", "grupo", "home", "industria", "industrial", "industrias", "international", "japan",
    "koninklijke", "national", "new", "north", "northern", "public", "royal", "societe", "south", "southern",
    "sunny", "taiwan", "texas", "union", "united", "universal", "west", "western",
})
MIN_NAME = 3
"""A shorter name ("BT") matches every headline with those letters: the fuller one is searched."""


def is_listing_code(name: str) -> bool:
    """An exchange's code for a line ("SAMS.EL.0,5SP.GDRS144A/95", "SHINKO EL.IND.LTD"), not a name:
    no lowercase letter and an abbreviation dot, or a slash or digit-comma."""
    return (not re.search(r"[a-z]", name) and "." in name) or bool(re.search(r"/|\d,\d", name))


def default_search(name: str) -> SearchName:
    if is_listing_code(name):
        return SearchName("", (), skip=True, note="a listing code, not a name headlines use")
    base = name.split(" - ", 1)[0].strip()
    base = re.sub(r"^the\s+", "", _LISTING_WORDS.sub("", base), flags=re.IGNORECASE)
    base = _LEFTOVERS.sub("", base.strip(" ,")).strip(" ,")
    short = _LEFTOVERS.sub("", short_name(base)).strip(" ,")
    words = short if len(short) >= MIN_NAME or len(base) < MIN_NAME else base
    first = words.split()[0]
    usable = len(first) >= 4 and first != words and first.casefold() not in _GENERIC
    aliases = tuple(dict.fromkeys([words, *([first] if usable else [])]))
    if " " not in words and (len(words) <= SHORT_NAME or words.casefold() in _COMMON_WORDS):
        return SearchName(f'"{words}" {DEFAULT_CONTEXT}', aliases, note="a short or common word: context words added")
    return SearchName(f'"{words}"', aliases)


def mentions(title: str, aliases: tuple[str, ...]) -> bool:
    """Whether a headline names the company as a word: a short all-capitals alias ("MAN", "BP")
    only in capitals, any other in any case."""
    for alias in aliases:
        flags = 0 if alias.isupper() and len(alias) <= 4 else re.IGNORECASE
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", title, flags):
            return True
    return False
