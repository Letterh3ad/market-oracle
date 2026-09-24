from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Set
from dataclasses import dataclass, replace

from oracle.universe.exchanges import exchange_country, exchange_rank

# Real companies reach ~25 lines on one exchange (Freddie Mac's preferred series);
# FinanceDatabase's placeholders and misassigned ISINs run from 34 to 96.
MAX_LINES_PER_EXCHANGE = 30

# Legal forms as normalise_name leaves them ("p.l.c." is "plc"). Only legal forms: "Holdings"
# or "Group" name a different entity as often as the same one.
LEGAL_FORMS = frozenset({
    "plc", "inc", "incorporated", "corp", "corporation", "co", "ltd", "limited", "ag", "sa", "nv", "se",
    "spa", "ab", "asa", "oyj", "kk",
})

# FinanceDatabase's bands, largest first.
MARKET_CAP_BANDS = ("Mega Cap", "Large Cap", "Mid Cap", "Small Cap", "Micro Cap", "Nano Cap")
_BAND_FLOORS_USD = (200e9, 10e9, 2e9, 300e6, 50e6, 0.0)
"""Where each band starts, as FinanceDatabase cuts them."""


def band_of_usd(market_cap: float) -> str:
    """The band a market cap in US dollars falls in."""
    return next(band for band, floor in zip(MARKET_CAP_BANDS, _BAND_FLOORS_USD) if market_cap >= floor)


SIZES: dict[str, tuple[str, ...]] = {"large": MARKET_CAP_BANDS[:2], "mid-small": MARKET_CAP_BANDS[2:]}
"""The two sizes a comparison splits companies into, by band."""


def size_of(band: str | None) -> str | None:
    """The SIZES key a band falls in; None for no known band."""
    return next((size for size, bands in SIZES.items() if band in bands), None)


@dataclass(frozen=True)
class Listing:
    """One tradable line: a company's shares on one exchange under one symbol."""

    symbol: str
    name: str
    type: str
    country: str | None = None
    exchange: str | None = None
    isin: str | None = None
    delisted: bool = False
    market: str | None = None
    currency: str | None = None
    sector: str | None = None
    industry_group: str | None = None
    industry: str | None = None
    market_cap: str | None = None
    website: str | None = None
    summary: str | None = None
    category_group: str | None = None
    category: str | None = None
    family: str | None = None
    company_key: str = ""
    is_primary: bool = False


def normalise_name(name: str) -> str:
    # Punctuation is dropped rather than spaced so "p.l.c." and "PLC" agree.
    return " ".join(re.sub(r"[^\w\s]", "", name.casefold()).split())


# Sao Paulo ends a BDR's name in "DRN", sometimes glued on ("ECOPETROL SADRN").
_RECEIPT = re.compile(r"\b(?:[ABCG]DRS?|CEDEARS?)\b|DRN\b", re.IGNORECASE)
# B3 gives BDRs four letters or digits and 32, 33, 34, 35 or 39 (C1HI34.SA is Sinopec's).
_B3_RECEIPT = re.compile(r"^[A-Z0-9]{4}3[23459]\.SA$", re.IGNORECASE)


def is_receipt(name: str) -> bool:
    """A depositary receipt line: it carries its own ISIN, so it repeats a company listed
    elsewhere (Samsung's GDRs; Brazil's BDRs, Canada's CDRs, Argentina's CEDEARs) as if it
    were another."""
    return _RECEIPT.search(name) is not None


def is_receipt_symbol(symbol: str) -> bool:
    """A depositary receipt told by its ticker, whatever its name says."""
    return _B3_RECEIPT.match(symbol) is not None


def blind_name(name: str) -> str:
    """The normalised name without trailing legal forms, keeping at least one word."""
    return " ".join(_split_legal(name)[0])


def legal_form(name: str) -> str:
    """The legal forms blind_name strips, abbreviations spelled out ("Co., Ltd." is "co limited")."""
    return " ".join(_SPELLED_OUT.get(w, w) for w in _split_legal(name)[1])


_SPELLED_OUT = {"corp": "corporation", "inc": "incorporated", "ltd": "limited"}


def _split_legal(name: str) -> tuple[list[str], list[str]]:
    words = normalise_name(name).split()
    cut = len(words)
    while cut > 1 and words[cut - 1] in LEGAL_FORMS:
        cut -= 1
    return words[:cut], words[cut:]


def group_companies(listings: Iterable[Listing], dead: Set[str] = frozenset(),
                    suffix_blind: bool = False) -> list[Listing]:
    """Give every listing a company key and mark one primary listing per company (never a
    `dead` one, no price source has data for, while the company has another).

    Listings of one type are one company when they share an ISIN or a normalised name and country,
    transitively. Neither alone is enough: most listings carry no ISIN, and some carry
    another line's (German Siemens Healthineers listings hold the US ADR's ISIN).
    A name or ISIN spread over more than MAX_LINES_PER_EXCHANGE lines of one exchange
    is a placeholder or a parent's code on its subsidiaries, and links nothing.
    `suffix_blind` then also joins names equal without their legal forms (see _join_blind).
    """
    items = list(listings)
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    links = [_links(x) for x in items]
    per_exchange = Counter((link, x.exchange) for x, ls in zip(items, links) for link in ls)
    untrusted = {link for (link, _), n in per_exchange.items() if n > MAX_LINES_PER_EXCHANGE}

    first_seen: dict[tuple, int] = {}
    for i, ls in enumerate(links):
        for link in ls:
            if link not in untrusted:
                parent[find(i)] = find(first_seen.setdefault(link, i))

    if suffix_blind:
        _join_blind(items, find, parent, untrusted)

    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)

    out = list(items)
    for members in groups.values():
        rows = [items[i] for i in members]
        key = _company_key(rows, untrusted)
        primary = pick_primary(rows, dead).symbol
        for i in members:
            out[i] = replace(items[i], company_key=key, is_primary=items[i].symbol == primary)
    return out


def blind_link(x: Listing) -> tuple | None:
    name = blind_name(x.name)
    return ("blind", name, x.country or "", x.type) if name else None


def _join_blind(items: list[Listing], find, parent: list[int], untrusted: set[tuple]) -> None:
    """Join companies whose names match without legal forms, unless any two of those a join
    would bridge conflict: both carry trusted ISINs and share none, or both already have a
    line on one exchange (two home lines on one exchange are two companies). A conflict
    skips every join it touches, so the result never depends on row order."""
    links = [blind_link(x) for x in items]
    per_exchange = Counter((link, x.exchange) for x, link in zip(items, links) if link)
    crowded = {link for (link, _), n in per_exchange.items() if n > MAX_LINES_PER_EXCHANGE}
    isins: dict[int, set[str]] = {}
    exchanges: dict[int, set[str]] = {}
    roots: dict[tuple, set[int]] = {}
    for i, (x, link) in enumerate(zip(items, links)):
        r = find(i)
        if x.isin and ("isin", x.isin, x.type) not in untrusted:
            isins.setdefault(r, set()).add(x.isin)
        if x.exchange:
            exchanges.setdefault(r, set()).add(x.exchange)
        if link is not None and link not in crowded:
            roots.setdefault(link, set()).add(r)

    def clash(group: set[int]) -> bool:
        seen_isins: list[set[str]] = []
        seen_exchanges: set[str] = set()
        for r in group:
            mine, ex = isins.get(r, set()), exchanges.get(r, set())
            if ex & seen_exchanges or (mine and any(o and not mine & o for o in seen_isins)):
                return True
            seen_isins.append(mine)
            seen_exchanges |= ex
        return False

    # Links that pass alone can still chain two clashing companies, so check the chains too.
    joins = [group for group in roots.values() if len(group) > 1 and not clash(group)]
    chain = {r: r for group in joins for r in group}

    def top(r: int) -> int:
        while chain[r] != r:
            r = chain[r]
        return r

    for group in joins:
        first, *rest = sorted(group)
        for r in rest:
            chain[top(r)] = top(first)
    chains: dict[int, set[int]] = {}
    for r in chain:
        chains.setdefault(top(r), set()).add(r)
    for group in chains.values():
        if not clash(group):
            first, *rest = sorted(group)
            for r in rest:
                parent[r] = first


def _links(x: Listing) -> list[tuple]:
    # Typed, so a fund never merges with a company.
    links: list[tuple] = []
    if x.isin:
        links.append(("isin", x.isin, x.type))
    name = normalise_name(x.name)
    if name:
        links.append(("name", name, x.country or "", x.type))
    return links


def _company_key(rows: list[Listing], untrusted: set[tuple]) -> str:
    # Only a trusted link names a company: an untrusted one is shared by several.
    trusted = sorted(link for x in rows for link in _links(x) if link not in untrusted)
    if trusted:
        kind, *value = trusted[0]
        return f"{kind}:{'|'.join(value)}"
    return f"symbol:{min(x.symbol for x in rows)}"


def pick_primary(rows: list[Listing], dead: Set[str] = frozenset()) -> Listing:
    return rank_listings(rows, dead)[0]


def rank_listings(rows: list[Listing], dead: Set[str] = frozenset()) -> list[Listing]:
    """A company's listings best first: the first is its primary."""
    home = _home_country(rows)
    return sorted(
        rows,
        key=lambda x: (
            x.delisted,
            x.symbol in dead,
            exchange_country(x.exchange) != home,
            exchange_rank(x.exchange),
            x.symbol,
        ),
    )


def _home_country(rows: list[Listing]) -> str | None:
    counts = Counter(x.country for x in rows if x.country)
    if not counts:
        return None
    return min(counts, key=lambda c: (-counts[c], c))
