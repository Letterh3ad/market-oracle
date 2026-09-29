"""The live regime account: each step builds one regime book from stored bars (the traded
listings and the whole stored market, for the mood) and trades its leans under the strategy's
rules, logging each decision with what its lean was made of and saving the day's reading for
the Ledgers card."""

from __future__ import annotations

import dataclasses
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from oracle.broker.orders import Action
from oracle.broker.regime import (
    LABEL_SCORES, Explained, RegimeBook, RegimeInputs, RegimeState, regime_book, settings_from,
)
from oracle.broker.rules import Decision, Lean
from oracle.services.context import AppContext
from oracle.services.events_service import stored_releases
from oracle.services.paper_service import GONE, REGIME, discovered_off, step_account, traded_by, verdict_history
from oracle.services.prices_service import ConvertedBar, histories_in_base
from oracle.services.watch_service import discovered_only
from oracle.store.db import to_utc_iso
from oracle.store.paper import DecisionRepo, LoggedDecision
from oracle.store.regime import RegimeStateRepo
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo

__all__ = ["REGIME", "REGIME_OFF", "RegimeCard", "RegimeRun", "build_book", "regime_card", "regime_step"]

REGIME_OFF = ("the regime account is off: nothing was decided. Turn it on on the Ledgers tab or in Settings "
              "([regime_account] enabled)")
DISCOVERED_OFF = discovered_off("the regime account")
NO_MOOD = "no market mood yet: under a year of stored market history, so the regime line leans nothing"
NO_SCORE = "no regime score: too little history for any price method"
HISTORY_DAYS = 1100
"""Calendar days of bars each step reads: the mood's 200-day average and its trailing year of
volatility and correlation percentiles, the 12-month return and the health record all fit,
with room; older bars change nothing and only cost time."""
STALE_DAYS = 7
"""A listing whose last bar is this far behind the market's is not leaned on: its prices are stale."""
COMPLETE_SHARE = 0.8
"""A trailing stored day on which fewer of the traded listings have a bar than this share of the
recent busiest day is a session still open somewhere (another exchange has not closed): the step
reads up to the day before it. Only the traded listings count: the rest of the stored market
(a replay study's, ones no longer watched) stops getting bars and would make every day look
partial."""
RECENT_DAYS = 10
FACTOR_ANALYSTS = ("value", "quality")


@dataclass(frozen=True)
class RegimeRun:
    day: date | None
    """The market day read: the last complete stored session before today."""
    mood: str | None
    flags: tuple[str, ...]
    decisions: list[Decision]


@dataclass(frozen=True)
class RegimeCard:
    enabled: bool
    note: str | None
    """Why the card has less to show: off, never stepped, or no mood on the last step."""
    day: date | None
    at: str | None
    """When the step that read `state` ran."""
    state: dict | None
    decisions: tuple[LoggedDecision, ...]


def build_book(ctx: AppContext, history: Mapping[str, list[ConvertedBar]], inputs: RegimeInputs) -> RegimeBook:
    """The regime book over `history`, with the `[regime]` settings and the strategy's limits."""
    settings = settings_from(asdict(ctx.config.regime), max_positions=ctx.config.strategy.max_positions,
                             min_probability=ctx.config.strategy.min_probability)
    return regime_book(history, inputs=inputs, settings=settings)


def regime_step(ctx: AppContext) -> RegimeRun | None:
    """The regime book's leans traded under the strategy's rules on the regime account; None,
    deciding nothing, while it is off. Reads only sessions before today (UTC): a bar is not
    known to be final until its exchange has closed, and bars carry no closing time, so today's
    are never read, whatever time the tick runs."""
    if not ctx.config.regime_account.enabled:
        return None
    last = ctx.clock.utcnow().date() - timedelta(days=1)
    traded = traded_by(ctx, REGIME)
    history, day = complete_sessions(histories_in_base(ctx, last - timedelta(days=HISTORY_DAYS), last), traded)
    book = build_book(ctx, history, _inputs(ctx, traded))
    state = book.state(day) if day else None
    found, skips, why = _leans(ctx, book, state, day, traded, history)
    skips += [Decision(s, Action.SKIP, None, DISCOVERED_OFF) for s in discovered_only(ctx) if s not in traded]
    decisions = step_account(ctx, REGIME, found, skips, {}, why, book.cap)
    if day is not None:
        reading = {**_plain(state), "unscored": _unscored(state, why)} if state else {"mood": None, "note": NO_MOOD}
        RegimeStateRepo(ctx.conn).save(REGIME, day, reading, to_utc_iso(ctx.clock.utcnow()))
    return RegimeRun(day, state.mood if state else None, tuple(sorted(state.flags)) if state else (), decisions)


def _unscored(state: RegimeState, why: Mapping[str, dict]) -> list[str]:
    """Weighted methods no traded listing had a score from, so the card can say its weight went
    to the others (earnings drift, while no earnings dates are stored)."""
    if not why:
        return []
    scored = {m for x in why.values() for m in x["parts"]}
    return sorted(m for m, w in state.weights.items() if w > 0 and m not in scored)


def complete_sessions(history: Mapping[str, list[ConvertedBar]],
                      traded: Collection[str]) -> tuple[dict[str, list[ConvertedBar]], date | None]:
    """`history` without its trailing days partial among `traded`, and the last complete day
    (None without bars for any traded listing)."""
    counts: dict[date, int] = {}
    for symbol in traded:
        for c in history.get(symbol, ()):
            counts[c.bar.date] = counts.get(c.bar.date, 0) + 1
    days = sorted(counts)
    busiest = max((counts[d] for d in days[-RECENT_DAYS:]), default=0)
    while days and counts[days[-1]] < COMPLETE_SHARE * busiest:
        days.pop()
    if not days:
        return {}, None
    last = days[-1]
    kept = {s: [c for c in rows if c.bar.date <= last] for s, rows in history.items()}
    return {s: rows for s, rows in kept.items() if rows}, last


def _leans(ctx: AppContext, book: RegimeBook, state: RegimeState | None, day: date | None, traded: list[str],
           history: Mapping[str, list[ConvertedBar]]) -> tuple[dict[str, Lean], list[Decision], dict[str, dict]]:
    universe = UniverseRepo(ctx.conn)
    found, skips, why = {}, [], {}
    for symbol in traded:
        rows = history.get(symbol)
        if universe.resolve(symbol) is None:
            skips.append(Decision(symbol, Action.SKIP, None, GONE))
        elif not rows:
            skips.append(Decision(symbol, Action.SKIP, None, "no prices stored: update its prices"))
        elif state is None:
            skips.append(Decision(symbol, Action.SKIP, None, NO_MOOD))
        elif (last := rows[-1].bar.date) < day - timedelta(days=STALE_DAYS):
            skips.append(Decision(symbol, Action.SKIP, None, f"no price since {last.isoformat()}: update its prices"))
        elif (explained := book.explain(symbol, day)) is None:
            skips.append(Decision(symbol, Action.SKIP, None, NO_SCORE))
        else:
            found[symbol] = explained.lean
            why[symbol] = _explanation(explained)
    return found, skips, why


def _explanation(x: Explained) -> dict:
    """The mood and every method's weighted part of the score, largest first."""
    ranked = sorted(x.parts.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    return {"mood": x.state.mood, "flags": sorted(x.state.flags), "label": x.lean.label,
            "probability": x.lean.probability, "score": x.score, "parts": dict(ranked)}


def _inputs(ctx: AppContext, traded: list[str]) -> RegimeInputs:
    universe, signals = UniverseRepo(ctx.conn), SignalRepo(ctx.conn)
    sectors = {s: p.sector for s in traded if (p := universe.profile(s)) and p.sector}
    earnings = {s: tuple((r.day, r.before_open) for r in found) for s in traded if (found := stored_releases(ctx, s))}
    factors = {s: dated for s in traded
               if (dated := _combined({a: [(x.as_of, x.score) for x in signals.history(s, a)] for a in FACTOR_ANALYSTS}))}
    verdicts = {s: tuple((d, LABEL_SCORES[label]) for d, label in found)
                for s, found in verdict_history(ctx, traded).items()}
    return RegimeInputs(sectors, earnings, factors, verdicts, ctx.config.ai_account.max_verdict_age_days)


def _combined(series: Mapping[str, list[tuple[date, float]]]) -> tuple[tuple[date, float], ...]:
    """On each day any analyst scored, the mean of each one's newest score known by then."""
    latest: dict[str, float] = {}
    out = []
    for day, name, score in sorted((d, a, v) for a, rows in series.items() for d, v in rows):
        latest[name] = score
        if out and out[-1][0] == day:
            out.pop()
        out.append((day, sum(latest.values()) / len(latest)))
    return tuple(out)


def _plain(value):
    """A regime reading as JSON: sets sorted, days as ISO text, mappings as objects."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def regime_card(ctx: AppContext, limit: int = 20) -> RegimeCard:
    """The last step's regime reading and the account's recent decisions with their explanations."""
    enabled = ctx.config.regime_account.enabled
    stored = RegimeStateRepo(ctx.conn).latest(REGIME)
    decisions = tuple(DecisionRepo(ctx.conn).recent(REGIME, limit))
    if not enabled:
        note = REGIME_OFF
    elif stored is None:
        note = "no regime reading yet: it is read at each tick's regime step, or press Regime step"
    else:
        note = stored.state.get("note")
    return RegimeCard(enabled, note, stored.day if stored else None, stored.at if stored else None,
                      stored.state if stored else None, decisions)
