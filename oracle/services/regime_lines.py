"""The rules-only regime line where past days are replayed (the backtest and the replay study
account). One regime book over the whole stored market up to the last day replayed, so the mood
and the ranks are the market's, as the live account reads them, fed only inputs dated on or
before each day, with a caption that names exactly the inputs it had."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from oracle.broker.regime import DatedScores, RegimeBook, RegimeInputs, regime_book, settings_from
from oracle.broker.rules import Rules
from oracle.services.context import AppContext
from oracle.services.events_service import stored_releases
from oracle.services.prices_service import histories_in_base
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo

__all__ = ["HISTORY_DAYS", "RegimeLine", "combined", "regime_line"]

HISTORY_DAYS = 1100
"""Calendar days of market bars read before the first day replayed: the mood's 200-day average
and trailing year of percentiles, the 12-month return and the health record all fit, with room.
The live account reads the same span back from today."""
FACTOR_ANALYSTS = ("value", "quality")


@dataclass(frozen=True)
class RegimeLine:
    book: RegimeBook
    caption: str


def regime_line(ctx: AppContext, start: date, end: date, rules: Rules,
                verdicts: Mapping[str, DatedScores] | None = None) -> RegimeLine:
    """The book over every stored listing's bars from HISTORY_DAYS before `start` to `end`
    (never after), in the base currency. `verdicts` feeds the `ai_verdict` method (the replay
    study's, dated at their as-of day); None where there are none to read (a plain backtest).
    Earnings days come from stored SEC releases only (nothing is fetched); value and quality
    only from the stored signals, each from the day it was recorded."""
    market = histories_in_base(ctx, start - timedelta(days=HISTORY_DAYS), end)
    universe, signals = UniverseRepo(ctx.conn), SignalRepo(ctx.conn)
    sectors = {s: p.sector for s in market if (p := universe.profile(s)) and p.sector}
    earnings = {s: found for s in market
                if (found := tuple((r.day, r.before_open) for r in stored_releases(ctx, s) if r.day <= end))}
    factors = {s: dated for s in market if (dated := combined(
        {a: [(d, v) for d, v in signals.recorded(s, a) if d <= end] for a in FACTOR_ANALYSTS}))}
    max_age = ctx.config.ai_account.max_verdict_age_days
    inputs = RegimeInputs(sectors, earnings, factors, dict(verdicts or {}), max_age)
    settings = settings_from(asdict(ctx.config.regime), max_positions=rules.max_positions,
                             min_probability=rules.min_probability)
    book = regime_book(market, inputs=inputs, settings=settings)
    return RegimeLine(book, _caption(len(market), earnings, factors, verdicts, max_age, end))


def combined(series: Mapping[str, list[tuple[date, float]]]) -> tuple[tuple[date, float], ...]:
    """On each day any analyst scored, the mean of each one's newest score known by then."""
    latest: dict[str, float] = {}
    out: list[tuple[date, float]] = []
    for day, name, score in sorted((d, a, v) for a, rows in series.items() for d, v in rows):
        latest[name] = score
        if out and out[-1][0] == day:
            out.pop()
        out.append((day, sum(latest.values()) / len(latest)))
    return tuple(out)


def _caption(listings: int, earnings: Mapping, factors: Mapping[str, DatedScores],
             verdicts: Mapping[str, DatedScores] | None, max_age: int, end: date) -> str:
    first = min((rows[0][0] for rows in factors.values()), default=None)
    fundamentals = (f"value and quality from the stored signals of {len(factors)} listing"
                    f"{'' if len(factors) == 1 else 's'}, each only from the day it was recorded (the first on "
                    f"{first.isoformat()}), none before" if first else
                    f"no value or quality (no stored signal recorded by {end.isoformat()})")
    drift = (f"earnings drift from the stored SEC release days of {len(earnings)} listing"
             f"{'' if len(earnings) == 1 else 's'} (none fetched)" if earnings else
             "no earnings drift (no stored SEC release days)")
    ai = (f"the replay verdicts as its AI input, each from its as-of day for up to {max_age} days" if verdicts
          else "no AI verdicts")
    return (f"Regime: trend, 12-1 momentum, short-term reversion, low volatility and a steady hold score, "
            f"weighted by the mood of the whole stored market ({listings} listings), capped per sector and traded "
            f"under the same rules, sizes and costs; {drift}; {fundamentals}; {ai}.")
