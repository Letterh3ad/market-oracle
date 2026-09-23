from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import timedelta

from oracle.analysts import factors
from oracle.analysts.indicators import closes
from oracle.analysts.risk import Series, risk
from oracle.analysts.signals import AnalystSignal, Direction, Skipped
from oracle.analysts.technicals import combine, macd_signal, rsi_signal, volatility_signal
from oracle.analysts.trend import trend
from oracle.broker.baseline import baseline_lean, inputs
from oracle.broker.rules import Lean
from oracle.market.sources import Instrument
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, NoPrices, ServiceError
from oracle.services.fundamentals_service import PEER_MAX_AGE, fetch_fundamentals
from oracle.services.jobs_service import single_flight
from oracle.services.peers_service import choose_peers, with_fundamentals
from oracle.services.prices_service import price_history_in_base
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.leans import LeanRepo
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo
from oracle.store.watchlists import WatchlistRepo

__all__ = [
    "Analysis", "AnalystSignal", "Direction", "Lean", "Skipped", "StoredSignal", "WatchedAnalysis", "analyse",
    "analyse_watched", "latest_lean", "latest_signals", "signal_history",
]

JOB = "analyse"
# Far longer than a run takes; a crashed holder is freed by the pid check instead.
LEASE_S = 1800.0
FACTOR_NAMES = tuple(f.name for f in factors.FACTORS)
# Display order: price-based first, then the peer comparisons.
ORDER = ("technicals", "trend", "rsi", "macd", "volatility", "risk", "value", "quality", "growth")


@dataclass(frozen=True)
class Analysis:
    symbol: str
    signals: tuple[AnalystSignal, ...]
    skipped: tuple[Skipped, ...]
    lean: Lean | None
    """The baseline lean; None without a technicals signal."""


@dataclass(frozen=True)
class StoredSignal:
    signal: AnalystSignal
    stale: bool
    """Price-based: a newer bar is stored. Factors: their fundamentals are over a week old."""


@dataclass(frozen=True)
class WatchedAnalysis:
    symbol: str
    analysis: Analysis | None
    error: str | None


async def analyse(ctx: AppContext, symbol: str) -> Analysis:
    """Run every analyst over a listing and store the signals. Prices must already be
    stored (`prices update`); the company's and its peers' fundamentals are fetched when
    out of date."""
    listing = resolve_symbol(ctx, symbol).symbol
    bars = BarRepo(ctx.conn).history(listing)
    if not bars:
        raise NoPrices(f"no prices stored for {listing}: run `oracle prices update {listing}`")
    results = [f(bars) for f in (trend, rsi_signal, macd_signal, volatility_signal)]
    results.append(combine([r for r in results if isinstance(r, AnalystSignal)], listing))
    results.append(_risk(ctx, listing))
    results += await _factors(ctx, listing)
    signals = tuple(r for r in results if isinstance(r, AnalystSignal))
    now = to_utc_iso(ctx.clock.utcnow())
    SignalRepo(ctx.conn).save(list(signals), now)
    lean = _baseline(ctx, listing, now)
    return Analysis(listing, signals, tuple(r for r in results if isinstance(r, Skipped)), lean)


def _baseline(ctx: AppContext, listing: str, computed_at: str) -> Lean | None:
    """From every stored signal still current, so a recent factor signal survives a run
    whose fundamentals fetch failed."""
    current = [s.signal for s in latest_signals(ctx, listing) if not s.stale]
    lean = baseline_lean(listing, current)
    if lean is not None:
        used = ", ".join(s.analyst for s in inputs(current))
        LeanRepo(ctx.conn).save([lean], f"from {used}", computed_at)
    return lean


async def _factors(ctx: AppContext, listing: str) -> list[AnalystSignal | Skipped]:
    try:
        choice = choose_peers(ctx, listing)
    except InvalidQuery as exc:
        return [Skipped(name, listing, str(exc)) for name in FACTOR_NAMES]
    company = Instrument(choice.company.primary_symbol, choice.company.primary_currency)
    # One fetch, so the company and its peers share a client and crumb.
    found = await fetch_fundamentals(ctx, {company: timedelta(0), **{i: PEER_MAX_AGE for i in choice.instruments()}})
    own, note = found[company.symbol]
    if own is None:
        return [Skipped(name, listing, note or "no fundamentals for the company") for name in FACTOR_NAMES]
    group = with_fundamentals(choice, found)
    peers = [p.fundamentals for p in group.peers if p.fundamentals is not None]
    # Stored under the listing analysed, like every other signal.
    own = replace(own, symbol=listing)
    return [factors.score(name, own, peers, group.basis) for name in FACTOR_NAMES]


def _risk(ctx: AppContext, listing: str) -> AnalystSignal | Skipped:
    mine = _base_series(ctx, listing)
    if not mine:
        return Skipped("risk", listing, "no exchange rates for its prices yet: run `oracle fx update`")
    universe = UniverseRepo(ctx.conn)
    # A watched symbol gone from the universe has no prices to compare; `watch show` flags it.
    others = {
        s: _base_series(ctx, s) for s in WatchlistRepo(ctx.conn).all_symbols()
        if s != listing and universe.resolve(s) is not None
    }
    return risk(listing, mine, others, currency=ctx.config.base_currency, **asdict(ctx.config.risk))


def _base_series(ctx: AppContext, symbol: str) -> Series:
    """Adjusted closes in the base currency; days without a rate are left out."""
    converted = [c for c in price_history_in_base(ctx, symbol) if c.rate]
    return [(c.bar.date, close / c.rate) for c, close in zip(converted, closes([c.bar for c in converted]))]


async def analyse_watched(ctx: AppContext) -> list[WatchedAnalysis]:
    """Every watched listing, in one process at a time; one failing never stops the rest."""
    with single_flight(ctx, JOB, LEASE_S) as held:
        out = []
        for symbol in WatchlistRepo(ctx.conn).all_symbols():
            held.check()
            try:
                out.append(WatchedAnalysis(symbol, await analyse(ctx, symbol), None))
            except ServiceError as exc:
                out.append(WatchedAnalysis(symbol, None, str(exc)))
    return out


def latest_signals(ctx: AppContext, symbol: str) -> list[StoredSignal]:
    """Each analyst's most recent stored signal, in display order. Reads only."""
    listing = resolve_symbol(ctx, symbol).symbol
    last_bar = BarRepo(ctx.conn).last_date(listing)
    today = ctx.clock.utcnow().date()

    def stale(s: AnalystSignal) -> bool:
        if s.analyst in FACTOR_NAMES:
            return today - s.as_of > PEER_MAX_AGE
        return last_bar is not None and last_bar > s.as_of

    found = sorted(SignalRepo(ctx.conn).latest(listing), key=lambda s: ORDER.index(s.analyst))
    return [StoredSignal(s, stale(s)) for s in found]


def latest_lean(ctx: AppContext, symbol: str) -> tuple[Lean | None, bool]:
    """The listing's latest lean, and whether it is older than the last stored bar (the
    strategy will not trade on it then). Reads only."""
    lean = LeanRepo(ctx.conn).latest(symbol)
    last_bar = BarRepo(ctx.conn).last_date(symbol)
    return lean, lean is not None and last_bar is not None and lean.as_of < last_bar


def signal_history(ctx: AppContext, symbol: str, analyst: str) -> list[AnalystSignal]:
    return SignalRepo(ctx.conn).history(resolve_symbol(ctx, symbol).symbol, analyst)
