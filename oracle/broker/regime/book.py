"""The regime book: every method's score per listing, the day's mood, the mood's weights
(preset x playbook x health x AI tilt, renormalised and smoothed) and the blend of the
scores into a lean that says what each method contributed. Built once from all history,
it reads only bars on or before the day it is asked about."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from oracle.analysts.indicators import closes
from oracle.analysts.price_factors import Ranked, ranked_at
from oracle.analysts.signals import NEUTRAL_BAND
from oracle.broker.backtest import LeanSource
from oracle.broker.baseline import _label
from oracle.broker.momentum import rank_day
from oracle.broker.rules import BUY_LABELS, Lean
from oracle.market.bars import Bar

from . import methods
from .methods import FACTOR_MAX_AGE, HOLD_SCORE, Listing
from .mood import Composite, Mood, step_returns
from .settings import METHODS, RegimeInputs, RegimeSettings

SOURCE = "regime"
PRICE_METHODS = frozenset({"trend", "momentum", "reversion", "low_vol"})
"""A listing needs one of these to lean: buy-and-hold's constant alone is no evidence."""


@dataclass(frozen=True)
class RegimeState:
    day: date
    mood: str
    """calm_up, volatile_up, calm_down or volatile_down."""
    flags: frozenset[str]
    """clustered, rebound, choppy."""
    readings: Mapping[str, float]
    """trend_gap, vol_pct, ret_12m, ret_1m, corr_pct (when a correlation sample was found)."""
    base_weights: Mapping[str, float]
    """The mood's preset, renormalised."""
    weights: Mapping[str, float]
    """Final and smoothed; sums to 1."""
    warnings: Mapping[str, tuple[str, ...]]
    """method -> firing playbook warnings."""
    good: Mapping[str, tuple[str, ...]]
    """method -> firing playbook good signs."""
    health: Mapping[str, float]
    """method -> its own record's multiplier (1.0 without one)."""
    tilt: Mapping[str, float]
    """method -> the AI's multiplier, capped; empty for the rules-only line."""


@dataclass(frozen=True)
class Explained:
    lean: Lean
    parts: Mapping[str, float]
    """method -> weighted contribution; they sum to `score`."""
    score: float
    scores: Mapping[str, float]
    """method -> its own score in [-1, 1], for the methods that had one."""
    state: RegimeState


Tilt = Callable[[RegimeState], Mapping[str, float]]
"""The AI's multiplier per method for the day, given the day's state before any tilt;
called once per trading day in order, capped to [1 - tilt_cap, 1 + tilt_cap]."""


class RegimeBook:
    def __init__(self, history: Mapping[str, Sequence], inputs: RegimeInputs, settings: RegimeSettings,
                 tilt: Tilt | None, source: str) -> None:
        self._bars = {s: [_bar(r) for r in rows] for s, rows in history.items() if rows}
        self._inputs, self._settings, self._tilt, self._source = inputs, settings, tilt, source
        self._composite = Composite({s: step_returns(_base(rows)) for s, rows in history.items() if rows})
        self._listings: dict[str, Listing] = {
            s: methods.with_reactions(methods.listing(s, bars), inputs.earnings.get(s, ()), self._composite.return_on)
            for s, bars in self._bars.items()}
        self._ranks: dict[date, dict[str, Ranked]] = {}
        self._states: list[RegimeState | None] = []
        self._smoothed: dict[str, float] | None = None

    def state(self, day: date) -> RegimeState | None:
        j = self._composite.index(day)
        if j is None:
            return None
        while len(self._states) <= j:
            self._states.append(self._next(len(self._states)))
        return self._states[j]

    def scores(self, symbol: str, day: date) -> dict[str, float]:
        """Each method's score for the listing on its last bar on or before `day`; empty
        without a price method's."""
        x = self._listings.get(symbol)
        i = x.at(day) if x else None
        if i is None:
            return {}
        ranked = self._ranked(rank_day(x.dates[i])).get(symbol)
        verdict_age = timedelta(days=self._inputs.verdict_max_age)
        found = {
            "trend": methods.trend(x, i), "momentum": methods.momentum(ranked),
            "reversion": methods.reversion(x, i), "low_vol": methods.low_vol(ranked),
            "quality_value": methods.dated(self._inputs.factors.get(symbol, ()), x.dates[i], FACTOR_MAX_AGE),
            "earnings_drift": methods.earnings_drift(x, i), "hold": HOLD_SCORE,
            "ai_verdict": methods.dated(self._inputs.verdicts.get(symbol, ()), x.dates[i], verdict_age)}
        out = {m: v for m, v in found.items() if v is not None}
        return out if out.keys() & PRICE_METHODS else {}

    def explain(self, symbol: str, day: date) -> Explained | None:
        x = self._listings.get(symbol)
        i = x.at(day) if x else None
        if i is None:
            return None
        as_of = x.dates[i]
        state = self.state(as_of)
        scores = self.scores(symbol, as_of)
        if state is None or not scores:
            return None
        used = {m: state.weights[m] for m in scores if state.weights[m] > 0}
        total = sum(used.values())
        if total <= 0:
            return None
        parts = {m: w * scores[m] / total for m, w in used.items()}
        score = sum(parts.values())
        lean = Lean(symbol, as_of, _label(score), self._probability(score), self._source)
        return Explained(lean, parts, score, scores, state)

    def lean_source(self) -> LeanSource:
        """Reads the book's own history up to the last bar given, so the replay's LOOKBACK
        window does not shorten the 200-day average or the year of ranks."""
        def lean(symbol: str, bars: Sequence[Bar]) -> Lean | None:
            explained = self.explain(symbol, bars[-1].date) if bars else None
            return explained.lean if explained else None
        return lean

    def cap(self, leans: Mapping[str, Lean], held: Collection[str]) -> dict[str, Lean]:
        """`held` is every holding and pending buy: they count against their sector's cap."""
        limit = math.ceil(self._settings.sector_share * self._settings.max_positions)
        return sector_cap(leans, held, self._inputs.sectors, limit)

    def _ranked(self, day: date) -> dict[str, Ranked]:
        if day not in self._ranks:
            self._ranks[day] = ranked_at(self._bars, day)
        return self._ranks[day]

    def _probability(self, score: float) -> float:
        """A buy clears the rules' threshold and rises with the score, so the rules buy the
        strongest first; other labels are never bought, so theirs is only a strength."""
        if score >= NEUTRAL_BAND:
            low = self._settings.min_probability
            return round(min(1.0, low + (1 - low) * (score - NEUTRAL_BAND) / (1 - NEUTRAL_BAND)), 6)
        return round(0.5 + 0.5 * abs(score), 6)

    def _next(self, j: int) -> RegimeState | None:
        mood = self._composite.moods[j]
        if mood is None:
            self._smoothed = None
            return None
        base = _normalised(self._settings.weights[mood.mood])
        warnings, good = self._playbook(mood)
        health = self._health(j)
        s = self._settings
        raw = {m: base[m] * _playbook_factor(len(warnings.get(m, ())), len(good.get(m, ())), s) * health[m]
               for m in METHODS}
        state = RegimeState(self._composite.dates[j], mood.mood, mood.flags, mood.readings, base,
                            self._smooth(raw, base), warnings, good, health, {})
        if self._tilt is not None:
            tilt = {m: min(1 + s.tilt_cap, max(1 - s.tilt_cap, float(v)))
                    for m, v in self._tilt(state).items() if m in METHODS}
            state = replace(state, weights=self._smooth({m: raw[m] * tilt.get(m, 1.0) for m in METHODS}, base),
                            tilt=tilt)
        self._smoothed = dict(state.weights)
        return state

    def _smooth(self, raw: Mapping[str, float], base: Mapping[str, float]) -> dict[str, float]:
        today = _normalised(raw) if sum(raw.values()) > 0 else dict(base)
        if self._smoothed is None:
            return today
        a = 2 / (self._settings.span + 1)
        return {m: a * today[m] + (1 - a) * self._smoothed[m] for m in METHODS}

    def _playbook(self, mood: Mood) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
        """Firing warnings and good signs per method; none until ticket 02 writes the playbook."""
        return {}, {}

    def _health(self, j: int) -> dict[str, float]:
        """Each method's record multiplier; neutral until ticket 02 keeps the shadow records."""
        return dict.fromkeys(METHODS, 1.0)


def regime_book(history: Mapping[str, Sequence], *, inputs: RegimeInputs | None = None,
                settings: RegimeSettings | None = None, tilt: Tilt | None = None,
                source: str = SOURCE) -> RegimeBook:
    """`history` is every listing's bars oldest first, plain or priced (a `bar` and its
    `rate`, as the replay takes them): priced ones put the composite in the base currency. `tilt` is the AI's daily multipliers (regime-ai); None for rules only."""
    return RegimeBook(history, inputs or RegimeInputs(), settings or RegimeSettings(), tilt, source)


def sector_cap(leans: Mapping[str, Lean], held: Collection[str], sectors: Mapping[str, str],
               limit: int) -> dict[str, Lean]:
    """At most `limit` holdings plus new buys per sector, the likeliest buys first; the
    excess leans hold. A listing without a sector is not capped."""
    used = Counter(sectors[s] for s in set(held) if s in sectors)
    out = dict(leans)
    wanted = [x for x in leans.values() if x.label in BUY_LABELS and x.symbol not in held]
    for lean in sorted(wanted, key=lambda x: (-x.probability, x.symbol)):
        sector = sectors.get(lean.symbol)
        if sector is None:
            continue
        if used[sector] >= limit:
            out[lean.symbol] = replace(lean, label="hold")
        else:
            used[sector] += 1
    return out


def _playbook_factor(warnings: int, good: int, s: RegimeSettings) -> float:
    return min(s.playbook_cap, max(0.0, s.warning_factor ** warnings * s.good_factor ** good))


def _normalised(weights: Mapping[str, float]) -> dict[str, float]:
    total = sum(weights.get(m, 0.0) for m in METHODS)
    return {m: weights.get(m, 0.0) / total if total > 0 else 0.0 for m in METHODS}


def _bar(row) -> Bar:
    return row.bar if hasattr(row, "bar") else row


def _base(rows: Sequence) -> list[tuple[date, float]]:
    """Closes in the base currency where rates are given (a day without one left out)."""
    bars = [_bar(r) for r in rows]
    if not hasattr(rows[0], "bar"):
        return list(zip((b.date for b in bars), closes(bars)))
    return [(b.date, c / r.rate) for b, c, r in zip(bars, closes(bars), rows) if r.rate]
