from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oracle.market.fundamentals import (
    FIGURES,
    EodhdFundamentals,
    Figure,
    Fundamentals,
    FundamentalsSource,
    YahooFundamentals,
)
from oracle.market.fx import pick_rate
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.net.client import RateLimitedClient
from oracle.services.context import AppContext
from oracle.services.http import open_client
from oracle.services.prices_service import ConvertedBar, price_history_in_base
from oracle.services.sources import missing_key
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.fundamentals import FundamentalsRepo
from oracle.store.fx import FxRepo
from oracle.store.tags import TagRepo
from oracle.store.universe import UniverseRepo

__all__ = ["FIGURES", "CompanyProfile", "CompanyView", "Figure", "Fundamentals", "company_view"]

FUNDAMENTALS_SOURCES = ("yahoo", "eodhd")


@dataclass(frozen=True)
class CompanyProfile:
    symbol: str
    """The listing asked about."""
    primary_symbol: str
    name: str
    type: str
    country: str | None
    sector: str | None
    industry: str | None
    category: str | None
    family: str | None
    exchange: str | None
    listings: int
    tags: tuple[str, ...]
    website: str | None
    summary: str | None


@dataclass(frozen=True)
class CompanyView:
    profile: CompanyProfile
    price: ConvertedBar | None
    """The listing's latest stored bar; None until `oracle prices update`."""
    change_pct: float | None
    fundamentals: Fundamentals | None
    fundamentals_note: str | None
    """Why fundamentals are missing or older than today."""
    market_cap_base: float | None


async def company_view(ctx: AppContext, symbol: str) -> CompanyView:
    """Everything known about the company behind `symbol`. Fundamentals are refreshed at
    most daily, for the primary listing, and their failure never hides the rest."""
    listing = resolve_symbol(ctx, symbol)
    row = UniverseRepo(ctx.conn).profile(listing.symbol)
    profile = CompanyProfile(
        symbol=row["symbol"], primary_symbol=row["primary_symbol"], name=row["name"], type=row["type"],
        country=row["country"], sector=row["sector"], industry=row["industry"], category=row["category"],
        family=row["family"], exchange=row["exchange"], listings=row["listings"],
        tags=tuple(TagRepo(ctx.conn).of_company(row["company_key"])),
        website=row["website"], summary=row["summary"],
    )
    recent = price_history_in_base(ctx, listing.symbol, days=2)
    change = (recent[-1].bar.close / recent[0].bar.close - 1) * 100 if len(recent) == 2 else None
    fundamentals, note = await _fundamentals(ctx, Instrument(row["primary_symbol"], row["primary_currency"]))
    return CompanyView(
        profile=profile,
        price=recent[-1] if recent else None,
        change_pct=change,
        fundamentals=fundamentals,
        fundamentals_note=note,
        market_cap_base=_in_base(ctx, fundamentals),
    )


async def _fundamentals(ctx: AppContext, instrument: Instrument) -> tuple[Fundamentals | None, str | None]:
    repo = FundamentalsRepo(ctx.conn)
    today = ctx.clock.utcnow().date()
    stored = repo.latest(instrument.symbol)
    if stored is not None and stored.as_of >= today:
        return stored, None
    failures = []
    async with open_client(ctx) as client:
        for source in _sources(ctx, client):
            try:
                fresh = await source.fundamentals(instrument, today)
            except (NoData, SourceFailed) as exc:
                failures.append(f"{source.name}: {exc}")
                continue
            repo.save(fresh, to_utc_iso(ctx.clock.utcnow()))
            return fresh, None
    why = "; ".join(failures) or "no fundamentals source enabled"
    if stored is not None:
        return stored, f"could not refresh ({why}); showing {stored.as_of.isoformat()}"
    return None, f"no fundamentals ({why})"


def _in_base(ctx: AppContext, f: Fundamentals | None) -> float | None:
    if f is None or f.market_cap is None or f.currency is None:
        return None
    base = ctx.config.base_currency
    if f.currency == base:
        return f.market_cap
    rate = pick_rate(FxRepo(ctx.conn, base).history(f.currency), ctx.clock.utcnow().date())
    return f.market_cap / rate.units_per_base if rate else None


def _sources(ctx: AppContext, client: RateLimitedClient) -> list[FundamentalsSource]:
    build = {
        "yahoo": lambda: YahooFundamentals(client),
        "eodhd": lambda: EodhdFundamentals(client, ctx.config.keys.get("eodhd")),
    }
    names = [n for n in ctx.config.prices.sources if n in FUNDAMENTALS_SOURCES and not missing_key(ctx, n)]
    return [build[n]() for n in names]
