"""Lines FinanceDatabase lacks, added from Yahoo profiles of index members, and corrections to
lines it has wrong. Both are kept apart from its rows and laid over each refresh of them."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace

from oracle.market.bars import major_unit
from oracle.market.yahoo import YahooProfile
from oracle.universe.exchanges import exchange_country
from oracle.universe.listings import Listing, band_of_usd

BACKFILL = "backfill"

# Yahoo's eleven sectors under the GICS names FinanceDatabase uses.
YAHOO_SECTORS = {
    "Basic Materials": "Materials", "Communication Services": "Communication Services",
    "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples", "Energy": "Energy",
    "Financial Services": "Financials", "Healthcare": "Health Care", "Industrials": "Industrials",
    "Real Estate": "Real Estate", "Technology": "Information Technology", "Utilities": "Utilities",
}
_CLASSIFICATION = ("sector", "industry_group", "industry")


def listing_from_profile(p: YahooProfile, market: str | None, units_per_usd: float | None) -> Listing:
    """A home line from Yahoo's profile; `units_per_usd` (of the major unit of its currency)
    bands its market cap, which stays unbanded without one."""
    usd = usd_value(p, units_per_usd)
    return Listing(
        symbol=p.symbol, name=p.name or p.symbol, type="equity", country=p.country or exchange_country(p.exchange),
        exchange=p.exchange, market=market, currency=p.currency, sector=YAHOO_SECTORS.get(p.sector or ""),
        industry=p.industry, market_cap=band_of_usd(usd) if usd is not None else None, website=p.website,
        summary=p.summary, source=BACKFILL,
    )


# A real estate investment trust (or fund) is a company that owns property or mortgages.
_PROPERTY = re.compile(r"\b(?:reits?|real estate|realty|mortgage|propert(?:y|ies))\b", re.IGNORECASE)
_NAMED_TRUST = re.compile(r"\binvestment trust\b", re.IGNORECASE)
# What the line itself is, said near the start ("X is a closed ended equity mutual fund"),
# not what it runs or holds ("launches ... closed-end mutual funds"), sponsors ("a hedge fund
# manager") or serves ("a provider of corporate and fund services"). "A fund of funds" is one.
_IS_FUND = re.compile(
    r"\bis an? ((?:(?!(?:of|and) )[\w-]+ ){0,4}?(?:fund|investment trust)\b"
    r"(?! (?:sponsor|manager|management|services|administrat|compan))|(?:[\w-]+ ){0,2}closed[- ]end(?:ed)?\b)",
    re.IGNORECASE)
_OPENING = 200
# Yahoo names London trusts' shares "Ord"; "Growth & Income" is a trust's name, not a company's.
_FUND_WORD = re.compile(r"\b(trust|fund|ord|income)\b", re.IGNORECASE)


def closed_end_fund(name: str, industry: str | None, summary: str | None) -> str | None:
    """Why the line is an investment trust or other closed-end fund, which Yahoo types EQUITY,
    or None when it is a company: named one, its Yahoo summary opens by saying so, or Yahoo
    has no business profile for it (how it lists London's trusts) and a fund's word names it.
    Never a REIT or mortgage trust."""
    if (industry and _PROPERTY.search(industry)) or _PROPERTY.search(name):
        return None
    if _NAMED_TRUST.search(name):
        return "named an investment trust"
    if summary:
        said = _IS_FUND.search(summary[:_OPENING])
        return f"Yahoo: {said.group(0)}" if said and not _PROPERTY.search(said.group(1)) else None
    if not industry and (word := _FUND_WORD.search(name)):
        return f"no Yahoo profile, named {word.group(1)}"
    return None


def usd_value(p: YahooProfile, units_per_usd: float | None) -> float | None:
    return p.market_cap / units_per_usd if p.market_cap and units_per_usd else None


def major_currency(currency: str | None) -> str | None:
    return major_unit(currency)[0] if currency else None


def apply_backfill(listings: Iterable[Listing], added: Iterable[Listing],
                   corrections: Mapping[str, Mapping]) -> list[Listing]:
    """`listings` with `corrections` (symbol -> fields) laid over them, plus each added line they
    lack: once FinanceDatabase has a line itself, its row stands."""
    fixes = {s.casefold(): f for s, f in corrections.items()}
    out = [replace(x, **fixes[x.symbol.casefold()]) if x.symbol.casefold() in fixes else x for x in listings]
    have = {x.symbol.casefold() for x in out}
    return out + [x for x in added if x.symbol.casefold() not in have]


def inherit_classification(grouped: list[Listing]) -> list[Listing]:
    """Each added line takes its company's classification from a live FinanceDatabase line of
    it, so screens and peers read one vocabulary."""
    lender: dict[str, Listing] = {}
    for x in grouped:
        if x.source is None and not x.delisted and x.sector and x.company_key not in lender:
            lender[x.company_key] = x
    return [replace(x, **{f: getattr(lender[x.company_key], f) for f in _CLASSIFICATION})
            if x.source == BACKFILL and x.company_key in lender else x for x in grouped]
