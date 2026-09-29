"""What a regime book reads, shared by the live account and the replayed lines (the backtest and
the replay study account), so both measure the same book: the stored market it reads the mood,
ranks and health from, and its non-price inputs, each dated when it became known. Also the
replayed line itself, with a caption that names exactly the inputs it had."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from oracle.broker.regime import DatedScores, RegimeBook, RegimeInputs, Tilt, regime_book, settings_from
from oracle.broker.rules import Rules
from oracle.services.context import AppContext
from oracle.services.events_service import stored_releases
from oracle.services.never_list import never_company
from oracle.services.prices_service import ConvertedBar, histories_in_base
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo

__all__ = ["HISTORY_DAYS", "RegimeLine", "combined", "market_history", "regime_inputs", "regime_line"]

HISTORY_DAYS = 1100
"""Calendar days of market bars read before the first day decided: the mood's 200-day average
and trailing year of percentiles, the 12-month return and the health record all fit, with room;
older bars change nothing and only cost time."""
FACTOR_ANALYSTS = ("value", "quality")
MARKET_TYPE = "equity"
RANK_WAIT = timedelta(days=400)
"""About a year of bars and the next month-end: before it the 12-1 momentum rank does not exist."""


@dataclass(frozen=True)
class RegimeLine:
    book: RegimeBook
    caption: str


def market_history(ctx: AppContext, start: date, end: date,
                   traded: Collection[str]) -> dict[str, list[ConvertedBar]]:
    """The stored bars a book reads, `start` to `end`, base currency: every listing the universe
    calls an equity, plus the traded ones, never a never-listed company (Tesla). Funds are left
    out, as they would count the market twice in the mood and rank beside companies."""
    universe = UniverseRepo(ctx.conn)
    out = {}
    for s, rows in histories_in_base(ctx, start, end).items():
        profile = universe.profile(s)
        if s not in traded and (profile is None or profile.type.lower() != MARKET_TYPE):
            continue
        if profile is not None and never_company(ctx, s, user=False):
            continue
        out[s] = rows
    return out


def regime_inputs(ctx: AppContext, symbols: Collection[str], end: date,
                  verdicts: Mapping[str, DatedScores] | None = None) -> RegimeInputs:
    """The non-price inputs of every listing a book reads, known by `end`. Sectors from the
    universe; earnings days from stored SEC releases (never fetched); value and quality from
    the stored signals, each dated at the later of its as-of day and the day it was computed,
    so a past day never reads a score before it existed; `verdicts` as given (none to read in a
    plain backtest)."""
    universe, signals = UniverseRepo(ctx.conn), SignalRepo(ctx.conn)
    sectors = {s: p.sector for s in symbols if (p := universe.profile(s)) and p.sector}
    earnings = {s: found for s in symbols
                if (found := tuple((r.day, r.before_open) for r in stored_releases(ctx, s) if r.day <= end))}
    factors = {s: dated for s in symbols if (dated := combined(
        {a: [(d, v) for d, v in signals.recorded(s, a) if d <= end] for a in FACTOR_ANALYSTS}))}
    return RegimeInputs(sectors, earnings, factors, dict(verdicts or {}), ctx.config.ai_account.max_verdict_age_days)


def regime_line(ctx: AppContext, start: date, end: date, rules: Rules, traded: Collection[str],
                verdicts: Mapping[str, DatedScores] | None = None, tilt: Tilt | None = None) -> RegimeLine:
    """The replayed book over `market_history` from HISTORY_DAYS before `start` to `end` (never
    after). `verdicts` feeds the `ai_verdict` method (the replay study's, dated at their as-of
    day); None in a plain backtest. `tilt` makes it the regime-ai book (a replayed model tilt)."""
    market = market_history(ctx, start - timedelta(days=HISTORY_DAYS), end, traded)
    inputs = regime_inputs(ctx, market, end, verdicts)
    settings = settings_from(asdict(ctx.config.regime), max_positions=rules.max_positions,
                             min_probability=rules.min_probability)
    first = min((rows[0].bar.date for rows in market.values() if rows), default=start)
    return RegimeLine(regime_book(market, inputs=inputs, settings=settings, tilt=tilt),
                      _caption(len(market), inputs, verdicts is not None and bool(verdicts), end,
                               start < first + RANK_WAIT))


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


def _caption(listings: int, inputs: RegimeInputs, verdicts: bool, end: date, early: bool) -> str:
    factors, earnings = inputs.factors, inputs.earnings
    first = min((rows[0][0] for rows in factors.values()), default=None)
    fundamentals = (f"value and quality from the stored signals of {len(factors)} listing"
                    f"{'' if len(factors) == 1 else 's'}, each only from the day it was recorded (the first on "
                    f"{first.isoformat()}), none before" if first else
                    f"no value or quality (no stored signal recorded by {end.isoformat()})")
    drift = (f"earnings drift from the stored SEC release days of {len(earnings)} listing"
             f"{'' if len(earnings) == 1 else 's'} (none fetched)" if earnings else
             "no earnings drift (no stored SEC release days)")
    ai = (f"the replay verdicts as its AI input, each from its as-of day for up to {inputs.verdict_max_age} days"
          if verdicts else "no AI verdicts")
    wait = (" It starts within about a year of the first stored prices, before any 12-1 momentum rank exists, so "
            "its first buys rest on trend, reversion and low volatility while the momentum line waits for its "
            "first month-end rank." if early else "")
    return (f"Regime: trend, 12-1 momentum, short-term reversion, low volatility and a steady hold score, "
            f"weighted by the mood of the stored market ({listings} listings: companies only, Tesla and funds left out), "
            f"capped per sector and traded under the same rules, sizes and costs; {drift}; {fundamentals}; {ai}. "
            f"The mood, ranks and health read today's stored listings back in time, survivors and later picks "
            f"included, so the past market they see is kinder than the real one was.{wait}")
