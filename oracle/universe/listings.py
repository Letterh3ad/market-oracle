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


def blind_name(name: str) -> str:
    """The normalised name without trailing legal forms, keeping at least one word."""
    words = normalise_name(name).split()
    while len(words) > 1 and words[-1] in LEGAL_FORMS:
        words.pop()
    return " ".join(words)


def group_companies(listings: Iterable[Listing], dead: Set[str] = frozenset(),
                    suffix_blind: bool = False) -> list[Listing]:
    """Give every listing a company key and mark one primary listing per company (never a
    `dead` one, no price source has data for, while the company has another).

    Listings of one type are one company when they share an ISIN or a normalised name and country,
    transitively. Neither alone is enough: most listings carry no ISIN, and some carry
    another line's (German Siemens Healthineers listings hold the US ADR's ISIN).
    A name or ISIN spread over more than MAX_LINES_PER_EXCHANGE lines of one exchange
    is a placeholder or a parent's code on its subsidiaries, and links nothing.
    `suffix_blind` then also joins names equal without their legal forms (see blind_name),
    unless both sides already carry ISINs and share none.
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
    links = [blind_link(x) for x in items]
    per_exchange = Counter((link, x.exchange) for x, link in zip(items, links) if link)
    crowded = {link for (link, _), n in per_exchange.items() if n > MAX_LINES_PER_EXCHANGE}
    isins: dict[int, set[str]] = {}
    for i, x in enumerate(items):
        if x.isin and ("isin", x.isin, x.type) not in untrusted:
            isins.setdefault(find(i), set()).add(x.isin)
    first_seen: dict[tuple, int] = {}
    for i, link in enumerate(links):
        if link is None or link in crowded:
            continue
        a, b = find(i), find(first_seen.setdefault(link, i))
        mine, theirs = isins.get(a, set()), isins.get(b, set())
        if a == b or (mine and theirs and not mine & theirs):
            continue
        parent[a] = b
        isins[b] = mine | theirs


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
