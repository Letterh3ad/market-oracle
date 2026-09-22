from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# Company-level fields are read from the primary listing; exchange is per listing.
COMPANY_FIELDS = ("country", "sector", "industry", "market_cap")
FILTER_FIELDS = (*COMPANY_FIELDS, "exchange")
SORTS = ("relevance", "name", "symbol", *FILTER_FIELDS)


@dataclass(frozen=True)
class UniverseQuery:
    text: str = ""
    filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    sort: str | None = None
    """None: relevance with text, market cap without."""
    descending: bool = False
    all_listings: bool = False
    include_delisted: bool = False
    limit: int = 20
    offset: int = 0


def fts_match(text: str) -> str | None:
    """Each word as a quoted prefix term, so no user input can form FTS5 syntax."""
    words = re.findall(r"\w+", text)
    return " ".join(f'"{w}"*' for w in words) or None
