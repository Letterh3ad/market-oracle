from __future__ import annotations

from dataclasses import dataclass

from oracle.analysts.signals import AnalystSignal, Direction, Skipped
from oracle.analysts.technicals import combine, macd_signal, rsi_signal, volatility_signal
from oracle.analysts.trend import trend
from oracle.services.context import AppContext
from oracle.services.errors import NoPrices
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.signals import SignalRepo

__all__ = ["Analysis", "AnalystSignal", "Direction", "Skipped", "analyse", "signal_history"]


@dataclass(frozen=True)
class Analysis:
    symbol: str
    signals: tuple[AnalystSignal, ...]
    skipped: tuple[Skipped, ...]


def analyse(ctx: AppContext, symbol: str) -> Analysis:
    """Run every analyst over a listing's stored data and store the signals. Fetches
    nothing: prices come from `prices update`."""
    listing = resolve_symbol(ctx, symbol).symbol
    bars = BarRepo(ctx.conn).history(listing)
    if not bars:
        raise NoPrices(f"no prices stored for {listing}: run `oracle prices update {listing}`")
    results = [f(bars) for f in (trend, rsi_signal, macd_signal, volatility_signal)]
    results.append(combine([r for r in results if isinstance(r, AnalystSignal)]))
    signals = tuple(r for r in results if isinstance(r, AnalystSignal))
    SignalRepo(ctx.conn).save(list(signals), to_utc_iso(ctx.clock.utcnow()))
    return Analysis(listing, signals, tuple(r for r in results if isinstance(r, Skipped)))


def signal_history(ctx: AppContext, symbol: str, analyst: str) -> list[AnalystSignal]:
    return SignalRepo(ctx.conn).history(resolve_symbol(ctx, symbol).symbol, analyst)
