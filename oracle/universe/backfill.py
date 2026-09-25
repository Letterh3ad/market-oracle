"""Lines FinanceDatabase lacks, added from Yahoo profiles of index members, and corrections to
lines it has wrong. Both are kept apart from its rows and laid over each refresh of them."""

from __future__ import annotations

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
