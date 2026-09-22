from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from oracle.analysts import factors
from oracle.analysts.risk import Series, risk
from oracle.analysts.signals import AnalystSignal, Direction, Skipped
from oracle.analysts.technicals import combine, macd_signal, rsi_signal, volatility_signal
from oracle.analysts.trend import trend
from oracle.market.sources import Instrument
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fundamentals_service import fetch_fundamentals
from oracle.services.peers_service import peer_group
from oracle.services.prices_service import price_history_in_base
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo
from oracle.store.watchlists import WatchlistRepo

__all__ = ["Analysis", "AnalystSignal", "Direction", "Skipped", "analyse", "signal_history"]

FACTOR_ANALYSTS = (factors.value, factors.quality, factors.growth)


@dataclass(frozen=True)
class Analysis:
    symbol: str
    signals: tuple[AnalystSignal, ...]
    skipped: tuple[Skipped, ...]


async def analyse(ctx: AppContext, symbol: str) -> Analysis:
    """Run every analyst over a listing and store the signals. Prices must already be
    stored (`prices update`); the company's and its peers' fundamentals are fetched when
    out of date."""
    listing = resolve_symbol(ctx, symbol).symbol
    bars = BarRepo(ctx.conn).history(listing)
    if not bars:
        raise NoPrices(f"no prices stored for {listing}: run `oracle prices update {listing}`")
    results = [f(bars) for f in (trend, rsi_signal, macd_signal, volatility_signal)]
    results.append(combine([r for r in results if isinstance(r, AnalystSignal)]))
    results.append(_risk(ctx, listing))
    results += await _factors(ctx, listing)
    signals = tuple(r for r in results if isinstance(r, AnalystSignal))
    SignalRepo(ctx.conn).save(list(signals), to_utc_iso(ctx.clock.utcnow()))
    return Analysis(listing, signals, tuple(r for r in results if isinstance(r, Skipped)))


async def _factors(ctx: AppContext, listing: str) -> list[AnalystSignal | Skipped]:
    def skipped(reason: str) -> list[AnalystSignal | Skipped]:
        return [Skipped(f.__name__, listing, reason) for f in FACTOR_ANALYSTS]

    profile = UniverseRepo(ctx.conn).profile(listing)
    try:
        group = await peer_group(ctx, listing)
    except InvalidQuery as exc:
        return skipped(str(exc))
    instrument = Instrument(profile.primary_symbol, profile.primary_currency)
    company, note = (await fetch_fundamentals(ctx, [instrument]))[instrument.symbol]
    if company is None:
        return skipped(note or "no fundamentals for the company")
    peers = [p.fundamentals for p in group.peers if p.fundamentals is not None]
    # Stored under the listing analysed, like every other signal.
    company = replace(company, symbol=listing)
    return [f(company, peers, group.basis) for f in FACTOR_ANALYSTS]


def _risk(ctx: AppContext, listing: str) -> AnalystSignal | Skipped:
    mine = _base_series(ctx, listing)
    if not mine:
        return Skipped("risk", listing, "no exchange rates for its prices yet: run `oracle fx update`")
    others = {s: _base_series(ctx, s) for s in WatchlistRepo(ctx.conn).all_symbols() if s != listing}
    return risk(listing, mine, others, **asdict(ctx.config.risk))


def _base_series(ctx: AppContext, symbol: str) -> Series:
    """Adjusted closes in the base currency; days without a rate are left out."""
    return [
        (c.bar.date, (c.bar.adj_close if c.bar.adj_close is not None else c.bar.close) / c.rate)
        for c in price_history_in_base(ctx, symbol) if c.rate
    ]


def signal_history(ctx: AppContext, symbol: str, analyst: str) -> list[AnalystSignal]:
    return SignalRepo(ctx.conn).history(resolve_symbol(ctx, symbol).symbol, analyst)
