from __future__ import annotations

from dataclasses import asdict, dataclass

from oracle.market.fundamentals import FIGURES, Figure, Fundamentals
from oracle.market.fx import pick_rate
from oracle.market.sources import Instrument
from oracle.services.analysis_service import StoredSignal, latest_signals
from oracle.services.context import AppContext
from oracle.services.fundamentals_service import fetch_fundamentals
from oracle.services.fx_service import update_fx
from oracle.services.prices_service import ConvertedBar, day_change, price_history_in_base
from oracle.services.universe_service import resolve_symbol
from oracle.store.fundamentals import FundamentalsRepo
from oracle.store.fx import FxRepo
from oracle.store.tags import TagRepo
from oracle.store.universe import ListingProfile, UniverseRepo

__all__ = ["FIGURES", "CompanyProfile", "CompanyView", "Figure", "Fundamentals", "StoredSignal", "company_view"]


@dataclass(frozen=True)
class CompanyProfile(ListingProfile):
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyView:
    profile: CompanyProfile
    price: ConvertedBar | None
    """The listing's latest stored bar; None until `oracle prices update`."""
    day_change: float | None
    """Ratio: 0.05 is up 5%, like every other ratio here."""
    fundamentals: Fundamentals | None
    fundamentals_note: str | None
    """Why fundamentals are missing or older than today."""
    market_cap_base: float | None
    market_cap_rate_source: str | None
    """Where the rate converting market cap came from, for attribution."""
    signals: tuple[StoredSignal, ...] = ()
    """Each analyst's latest stored signal; empty until `oracle analyse`."""


async def company_view(ctx: AppContext, symbol: str, refresh: bool = True) -> CompanyView:
    """Everything known about the company behind `symbol`. With `refresh`, fundamentals
    older than today (for the primary listing) and a missing market-cap rate are fetched;
    without it, only stored data is read, so a screen can show at once. A failure to
    fetch never hides the rest."""
    listing = resolve_symbol(ctx, symbol)
    row = UniverseRepo(ctx.conn).profile(listing.symbol)
    profile = CompanyProfile(**asdict(row), tags=tuple(TagRepo(ctx.conn).of_company(row.company_key)))
    recent = price_history_in_base(ctx, listing.symbol, days=2)
    fundamentals, note = await _fundamentals(ctx, Instrument(row.primary_symbol, row.primary_currency), refresh)
    market_cap, rate_source = await _market_cap_in_base(ctx, fundamentals, refresh)
    return CompanyView(
        profile=profile,
        price=recent[-1] if recent else None,
        day_change=day_change(recent),
        fundamentals=fundamentals,
        fundamentals_note=note,
        market_cap_base=market_cap,
        market_cap_rate_source=rate_source,
        signals=tuple(latest_signals(ctx, listing.symbol)),
    )


async def _fundamentals(ctx: AppContext, instrument: Instrument, refresh: bool) -> tuple[Fundamentals | None, str | None]:
    if refresh:
        return (await fetch_fundamentals(ctx, [instrument]))[instrument.symbol]
    stored = FundamentalsRepo(ctx.conn).latest(instrument.symbol)
    return (stored, None) if stored is not None else (None, "fundamentals not fetched yet (refresh off)")


async def _market_cap_in_base(ctx: AppContext, f: Fundamentals | None, refresh: bool) -> tuple[float | None, str | None]:
    if f is None or f.market_cap is None or f.currency is None:
        return None, None
    base = ctx.config.base_currency
    if f.currency == base:
        return f.market_cap, None
    today = ctx.clock.utcnow().date()
    fx = FxRepo(ctx.conn, base)
    rate = pick_rate(fx.history(f.currency), today)
    if rate is None and refresh:
        # The primary listing's currency may be one no stored price uses yet.
        await update_fx(ctx, currencies=[f.currency], history=False)
        rate = pick_rate(fx.history(f.currency), today)
    return (f.market_cap / rate.units_per_base, rate.source) if rate else (None, None)
