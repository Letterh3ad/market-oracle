"""Laya's daily score as a regime method (ticket 08, DECISIONS 2026-10-01): built as 06's combined
score (the main run's features, each segment's purged trailing fit, `usable_day` per listing's
open) for any day, so the backtest and the live lines read the same thing; and its weight in
core-sat's satellite, which only the newest kept verdict for Laya's model gives, by a rule fixed
before any run.

Point in time: the score for decision day t reads articles usable at t's open and weights fitted
only on returns that ended before t's segment began; it enters the book dated t - 1, so a bar on
or after t - 1 reads it and its order fills at a later open."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from oracle.analysts.predictive import lookback, pct_ranks
from oracle.broker.regime import DatedScores, RegimeSettings
from oracle.broker.walk_forward import segments
from oracle.services.context import AppContext
from oracle.services.errors import UnknownWatchlist
from oracle.services.laya_service import MODEL_TAG, primary
from oracle.services.laya_test_service import (
    FIT_DAYS, HORIZONS, MAIN, MAIN_RELATIONS, TEST_START, LayaTest, Panel, build_panel, latest_verdict, observations,
    trailing_fit,
)
from oracle.services.watch_service import DEFAULT_LIST

__all__ = ["METHOD", "PRICE_METHODS", "LayaInput", "LayaVerdict", "daily_scores", "laya_input", "laya_only",
           "laya_verdict", "segment_start", "verdict_note", "with_laya"]

METHOD = "laya"
PRICE_METHODS = ("trend", "momentum", "reversion", "low_vol")
"""Laya's weight in a mood, when it earned one, is the mean of these methods' weights there."""


@dataclass(frozen=True)
class LayaVerdict:
    """What the newest kept main-run result for Laya's model says about its weight."""
    verdict: str | None
    """PASS, FAIL or INCONCLUSIVE; None with no result kept."""
    useful: bool
    """PASS and beating both baselines on both horizons: only then does it weigh in core-sat."""
    model: str
    test_id: int | None = None
    run_at: str | None = None
    why: str = ""


@dataclass(frozen=True)
class LayaInput:
    """Laya's dated scores for the listings asked about, and the verdict that sets its weight."""
    scores: Mapping[str, DatedScores]
    verdict: LayaVerdict


def laya_verdict(ctx: AppContext, model: str = MODEL_TAG) -> LayaVerdict:
    return _judged(latest_verdict(ctx, model, MAIN), model)


def _judged(test: LayaTest | None, model: str) -> LayaVerdict:
    if test is None:
        return LayaVerdict(None, False, model, why="no predictive test kept for this model: weight 0")
    beats = all(h.beats_baseline and h.beats_momentum for h in test.horizons)
    useful = test.passed and beats
    why = ("passed and beat both baselines" if useful else
           "passed the bar but did not beat both baselines: weight 0" if test.passed else
           f"{test.verdict}: weight 0")
    return LayaVerdict(test.verdict, useful, model, test.id, test.run_at, why)


def with_laya(settings: RegimeSettings, useful: bool) -> RegimeSettings:
    """The settings with Laya's row added: in each mood the mean of its price methods' weights
    when `useful`, else 0 (so the book is the one without it)."""
    return replace(settings, weights={
        mood: {**row, METHOD: sum(row.get(m, 0.0) for m in PRICE_METHODS) / len(PRICE_METHODS) if useful else 0.0}
        for mood, row in settings.weights.items()})


def laya_only(settings: RegimeSettings) -> RegimeSettings:
    """A book that weighs Laya's score alone (the laya account)."""
    return replace(settings, weights={mood: {**dict.fromkeys(row, 0.0), METHOD: 1.0}
                                      for mood, row in settings.weights.items()})


def verdict_note(v: LayaVerdict, settings: RegimeSettings | None = None) -> str:
    """One line: the verdict, and the weight it gives in core-sat."""
    head = (f"Laya's predictive test: {v.verdict} (test {v.test_id}, {v.run_at[:10]})" if v.verdict
            else "Laya's predictive test: none kept yet")
    if not v.useful:
        return f"{head}; {v.why.removesuffix(': weight 0')}: weight 0 in core-sat"
    row = with_laya(settings, True).weights if settings else {}
    parts = ", ".join(f"{m} {r[METHOD]:.3f}" for m, r in row.items())
    return f"{head}; {v.why}: weighs in core-sat's satellite" + (f" ({parts})" if parts else "")


def segment_start(day: date) -> date | None:
    """The start of 06's 6-month segment holding `day`; None before the first."""
    if day < TEST_START:
        return None
    return max(a for a, _ in segments(TEST_START, day + timedelta(days=200)) if a <= day)


def daily_scores(panel: Panel, first: date, last: date,
                 relations: Sequence[str] = MAIN_RELATIONS) -> dict[str, list[tuple[date, float]]]:
    """Each main company's score for every decision day from `first` to `last`, keyed by the day
    it enters a book (t - 1): per horizon the features' cross-sectional ranks weighted by the fit
    of t's segment, then the mean of the horizons that have a fit. Reads only `panel`, and for t
    only features usable by t's open and returns that ended before t's segment began."""
    names = [f"{r}_mean" for r in relations]
    starts = sorted({s for d in _days(first, last) if (s := segment_start(d)) is not None})
    if not starts:
        return {}
    fits: dict[tuple[int, date], dict[str, float]] = {}
    for h, lb in HORIZONS:
        obs, ics = observations(panel, h, lb, starts[0] - timedelta(days=FIT_DAYS), starts[-1], relations, names)
        for s in starts:
            fits[(h, s)] = trailing_fit(obs, ics, s, names)
    out: dict[str, list[tuple[date, float]]] = {m: [] for m in panel.mains}
    for t in _days(first, last):
        s = segment_start(t)
        if s is None:
            continue
        per: list[list[float]] = []
        for h, lb in HORIZONS:
            w = fits[(h, s)]
            if not any(w.values()):
                continue
            rows = [lookback(panel.daily.get(m, {}), t, lb, relations) for m in panel.mains]
            score = [0.0] * len(rows)
            for f in names:
                if w[f]:
                    score = [x + w[f] * (2 * r - 1) for x, r in zip(score, pct_ranks([row[f] for row in rows]))]
            per.append(score)
        if not per or not panel.mains:
            continue
        for k, m in enumerate(panel.mains):
            out[m].append((t - timedelta(days=1), round(sum(p[k] for p in per) / len(per), 6)))
    return {m: rows for m, rows in out.items() if rows}


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=k) for k in range((last - first).days + 1)]


def laya_input(ctx: AppContext, listings: Collection[str], first: date, last: date, *,
               watchlist: str = DEFAULT_LIST, model: str = MODEL_TAG, scores: bool = True) -> LayaInput:
    """Laya's scores for `listings` (a listing that is its company's primary line; another line
    of it has none, its open may differ) for decision days `first` to `last`, with the verdict.
    Without `scores` only the verdict is read (core-sat when Laya weighs nothing)."""
    verdict = laya_verdict(ctx, model)
    if not scores or not listings or (start := segment_start(max(first, TEST_START))) is None or last < TEST_START:
        return LayaInput({}, verdict)
    try:
        panel = build_panel(ctx, watchlist, start=start - timedelta(days=FIT_DAYS), end=last, model=model,
                            research=True)
    except UnknownWatchlist:
        return LayaInput({}, verdict)
    found = daily_scores(panel, max(first, TEST_START), last)
    by_company = {m.casefold(): rows for m, rows in found.items()}
    out = {}
    for s in listings:
        rows = by_company.get(s.casefold())
        if rows and primary(ctx, s).casefold() == s.casefold():
            out[s] = tuple(rows)
    return LayaInput(out, verdict)
