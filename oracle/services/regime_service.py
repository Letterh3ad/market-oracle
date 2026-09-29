"""The live regime account: each step builds one regime book from stored bars (the traded
listings and the whole stored market, for the mood) and trades its leans under the strategy's
rules, logging each decision with what its lean was made of and saving the day's reading for
the Ledgers card."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.broker.backtest import LOOKBACK
from oracle.broker.orders import Action
from oracle.broker.rules import Decision, Lean
from oracle.services.context import AppContext
from oracle.services.events_service import stored_releases
from oracle.services.paper_service import GONE, REGIME, _step, fresh_verdicts, traded_by
from oracle.services.prices_service import ConvertedBar, histories_in_base
from oracle.services.watch_service import discovered_only
from oracle.store.db import to_utc_iso
from oracle.store.paper import DecisionRepo, LoggedDecision
from oracle.store.regime import RegimeStateRepo
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo

__all__ = ["BookInputs", "REGIME", "REGIME_OFF", "RegimeCard", "RegimeRun", "build_book", "regime_card",
           "regime_step"]

REGIME_OFF = ("the regime account is off: nothing was decided. Turn it on on the Ledgers tab or in Settings "
              "([regime_account] enabled)")
DISCOVERED_OFF = ("discovered, and the regime account does not trade discovered companies: turn it on in "
                  "Settings ([discovery] ai_trades)")
NO_MOOD = "no market mood yet: under a year of stored market history, so the regime line leans nothing"
HISTORY_DAYS = 1100
"""Calendar days of bars each step reads: the mood's 200-day average and its trailing year of
volatility and correlation percentiles, the 12-month return and the health record all fit,
with room; older bars change nothing and only cost time."""
STALE_DAYS = 7
"""A listing whose last bar is this far behind the market's is not leaned on: its prices are stale."""
FACTOR_ANALYSTS = ("value", "quality")


@dataclass(frozen=True)
class BookInputs:
    """What the store gives the book beside prices, each keyed by listing."""
    sectors: Mapping[str, str]
    earnings: Mapping[str, tuple[date, ...]]
    """US earnings release days as last fetched (SEC 8-K item 2.02)."""
    factors: Mapping[str, Mapping[str, tuple[tuple[date, float], ...]]]
    """Analyst ("value", "quality") -> its stored scores, each dated by its last data point."""
    verdicts: Mapping[str, Lean]
    """The newest fresh research verdicts, by the AI account's freshness rule."""


@dataclass(frozen=True)
class RegimeRun:
    day: date | None
    """The market day read: the last stored bar on or before today."""
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


def build_book(ctx: AppContext, history: Mapping[str, list[ConvertedBar]], inputs: BookInputs):
    """The regime book over `history`, with the `[regime]` settings."""
    from oracle.broker import regime  # ticket 01's pure module; a top-level import once it lands

    return regime.regime_book(history, inputs=inputs, settings=ctx.config.regime)


def regime_step(ctx: AppContext) -> RegimeRun | None:
    """The regime book's leans traded under the strategy's rules on the regime account; None,
    deciding nothing, while it is off. Reads only bars on or before today."""
    if not ctx.config.regime_account.enabled:
        return None
    today = ctx.clock.utcnow().date()
    traded = traded_by(ctx, REGIME)
    history = {s: rows for s, rows in histories_in_base(ctx, today - timedelta(days=HISTORY_DAYS), today).items()
               if rows}
    day = max((rows[-1].bar.date for rows in history.values()), default=None)
    book = build_book(ctx, history, _inputs(ctx, traded))
    state = book.state(day) if day else None
    found, skips, why = _leans(ctx, book, state, day, traded, history)
    skips += [Decision(s, Action.SKIP, None, DISCOVERED_OFF) for s in discovered_only(ctx) if s not in traded]
    decisions = _step(ctx, REGIME, found, skips, {}, why)
    if day is not None:
        RegimeStateRepo(ctx.conn).save(REGIME, day, _plain(state) if state else {"mood": None, "note": NO_MOOD},
                                       to_utc_iso(ctx.clock.utcnow()))
    return RegimeRun(day, state.mood if state else None, tuple(sorted(state.flags)) if state else (), decisions)


def _leans(ctx: AppContext, book, state, day: date | None, traded: list[str],
           history: Mapping[str, list[ConvertedBar]]) -> tuple[dict[str, Lean], list[Decision], dict[str, dict]]:
    universe, lean_of = UniverseRepo(ctx.conn), book.lean_source()
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
        elif (lean := lean_of(symbol, [c.bar for c in rows[-LOOKBACK:]])) is None:
            skips.append(Decision(symbol, Action.SKIP, None, "no regime score: too little history for any method"))
        else:
            found[symbol] = lean
            explained = book.explain(symbol, last)
            if explained is not None:
                why[symbol] = _explanation(state, explained.lean, explained.parts)
    return found, skips, why


def _explanation(state, lean: Lean, parts: Mapping[str, float]) -> dict:
    """The mood and every method's weighted part of the score, largest first."""
    ranked = sorted(parts.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    return {"mood": state.mood, "flags": sorted(state.flags), "label": lean.label, "probability": lean.probability,
            "score": sum(parts.values()), "parts": dict(ranked)}


def _inputs(ctx: AppContext, traded: list[str]) -> BookInputs:
    universe, signals = UniverseRepo(ctx.conn), SignalRepo(ctx.conn)
    sectors = {s: p.sector for s in traded if (p := universe.profile(s)) and p.sector}
    earnings = {s: tuple(r.day for r in found) for s in traded if (found := stored_releases(ctx, s))}
    factors = {s: {a: tuple((x.as_of, x.score) for x in signals.history(s, a)) for a in FACTOR_ANALYSTS}
               for s in traded}
    return BookInputs(sectors, earnings, factors, fresh_verdicts(ctx, traded))


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
