"""The regime-ai line's daily tilt. Once per step day a model (local by default) reads the day's
regime state and multiplies each method's rules weight within the cap. Each day's answer, or
why there was none, is stored: a restart or a second step that day reuses it, and every later
book replays the tilts actually applied instead of asking the model about the past.

The prompt carries no date, so a replay (`ReplayTilt`, the backtest's regime_ai line) can ask
the same model about past days: it sees only the mood, readings, signs and health, nothing to
recall the future from, in the very prompt the live line gets."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import httpx

from oracle.broker.regime import METHODS, SIGN_INFO, RegimeState
from oracle.research.llm import ChatModel
from oracle.services.context import AppContext
from oracle.services.models_service import resolve_model, slot_profile
from oracle.store.db import to_utc_iso
from oracle.store.regime import StoredTilt, TiltRepo

__all__ = ["GIVE_UP_AFTER", "MAX_REASON", "RETRY_AFTER_S", "SYSTEM", "TILTED", "BadTilt", "DailyTilt", "ReplayStats",
           "ReplayTilt", "Stopped", "YIELD_STEP_S", "grounded", "tilt_messages", "tilt_model", "tilt_schema", "validated"]

log = logging.getLogger(__name__)

MAX_REASON = 300
"""Characters of the stored reason, guard notes included: the card shows one or two sentences."""
TILTED = tuple(m for m in METHODS if m != "ai_verdict")
"""The methods the model may tilt. Not the research verdicts: they come from the research model,
by default the same one, and a model boosting its own output measures nothing (gate 05 #1)."""
RETRY_AFTER_S = 45.0
"""Wait before the one retry of a call that got no answer at all (a timeout or no connection)."""
wait = time.sleep
"""How the retry waits; tests replace it, so none of them sleeps."""
YIELD_STEP_S = 5.0
"""How often a replay waiting for other model work looks again (and at its stop flag)."""
GIVE_UP_AFTER = 3
"""Days in a row a replay's model gives no answer at all before it stops asking: with Ollama
down, every later day would otherwise cost a retry wait for nothing."""

# Every word the model reads is here, so the prompt is changed in one place.
SYSTEM = (
    "You tune a paper-trading blend of stock-picking methods for one day. Each method has a weight from "
    "fixed rules: its preset for the market mood, times its playbook signs and its health. Multiply each "
    "method's weight by a number from {low} to {high}; 1 keeps the rules weight. Move a weight only for a "
    "firing warning or good sign, or a measured health far from 1; otherwise answer 1. A measured health far "
    "from 1 is a valid reason on its own: cut a method whose picks lag the market, raise one whose picks lead. "
    'Reply with JSON: every method key with its multiplier, and "reason": one or two sentences naming the '
    "main changes.")
MOODS = {"calm_up": "calm and rising market", "volatile_up": "volatile but rising market",
         "calm_down": "calm but falling market", "volatile_down": "volatile and falling market"}
FLAGS = {"clustered": "stocks moving together", "rebound": "sharp rebound after a falling year",
         "choppy": "the market keeps crossing its 200-day average"}
READINGS = {"trend_gap": "market vs its 200-day average", "vol_pct": "volatility percentile over the year",
            "corr_pct": "correlation percentile over the year", "ret_12m": "12-month market return",
            "ret_1m": "1-month market return", "move_pct": "size of the year's move against the 3 before"}
METHOD_NAMES = {"trend": "time-series trend", "momentum": "12-1 momentum", "reversion": "short-term mean reversion",
                "low_vol": "low volatility", "quality_value": "value and quality", "earnings_drift": "post-earnings drift",
                "hold": "buy and hold"}
HEALTH = ("Health is a method's own recent picks against the market: 0.5 lagging, 1.5 leading. "
          "A method with no record yet has no health.")


class BadTilt(ValueError):
    """The model answered, but not with a usable tilt."""


def tilt_messages(state: RegimeState, cap: float) -> list[dict]:
    """The day's state as a short prompt; the same state always gives the same text. No date:
    a replayed day must not let the model recall what came next."""
    flags = ", ".join(f"{f} ({FLAGS.get(f, f)})" for f in sorted(state.flags)) or "none"
    readings = ", ".join(f"{k} {v:.3f} ({READINGS.get(k, k)})" for k, v in sorted(state.readings.items())
                         if isinstance(v, (int, float)))
    lines = [f"Mood: {state.mood} ({MOODS.get(state.mood, state.mood)})",
             f"Flags: {flags}", f"Readings: {readings}", HEALTH, "Methods:"]
    lines += [_method(state, m) for m in TILTED]
    return [{"role": "system", "content": SYSTEM.format(low=_num(1 - cap), high=_num(1 + cap))},
            {"role": "user", "content": "\n".join(lines)}]


def _method(state: RegimeState, m: str) -> str:
    signs = [(name, True) for name in state.warnings.get(m, ())] + [(name, False) for name in state.good.get(m, ())]
    said = "; ".join(_sign(m, name, warning) for name, warning in signs) or "no signs"
    health = _health(state.health.get(m, 1.0)) if m in state.recorded else "no health record yet"
    return (f"- {m} ({METHOD_NAMES.get(m, m)}): preset {state.base_weights.get(m, 0):.3f}, "
            f"rules weight {state.weights.get(m, 0):.3f}, {health}; {said}")


HEALTH_WORDS = ((0.6, "its picks have lagged the market badly over the last ~60 days"),
                (0.9, "its picks have lagged the market over the last ~60 days"),
                (1.1, "its picks have about matched the market over the last ~60 days"),
                (1.4, "its picks have beaten the market over the last ~60 days"),
                (math.inf, "its picks have beaten the market well over the last ~60 days"))
"""Upper bound of each band and what it means, so a small model reads the number as a record."""


def _health(value: float) -> str:
    words = next(said for top, said in HEALTH_WORDS if value <= top)
    edge = " (the floor)" if value <= 0.5 else " (the ceiling)" if value >= 1.5 else ""
    return f"health {value:.2f}: {words}{edge}"


def _sign(method: str, name: str, warning: bool) -> str:
    sign = SIGN_INFO.get((method, name))
    what = f"{'warning' if warning else 'good sign'} {name}"
    return f"{what}: {sign.reason} ({sign.source})" if sign else what


def _num(v: float) -> str:
    return f"{v:g}"


def tilt_schema() -> dict:
    return {"type": "object", "properties": {**{m: {"type": "number"} for m in TILTED}, "reason": {"type": "string"}},
            "required": [*TILTED, "reason"]}


def validated(reply: object, cap: float) -> tuple[dict[str, float], str]:
    """Tiltable methods' finite numbers clipped to [1 - cap, 1 + cap], and the reason cut short;
    BadTilt when no tiltable method has a number or there is no reason."""
    if not isinstance(reply, Mapping):
        raise BadTilt(f"the model's answer was not a JSON object: {str(reply)[:80]!r}")
    reason = reply.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise BadTilt("the model gave no reason")
    tilt = {m: round(min(1 + cap, max(1 - cap, float(v))), 4) for m, v in reply.items()
            if m in TILTED and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    if not tilt:
        raise BadTilt("the model gave no multiplier for any known method")
    return tilt, _short(reason)


def grounded(tilt: Mapping[str, float], state: RegimeState) -> tuple[dict[str, float], list[str], list[str]]:
    """The tilt with each move kept only on a side of 1 its method's evidence supports; then the
    methods reset for having no evidence, and those reset for moving against it. A warning or a
    measured health below 1 allows a cut, a good sign or a measured health above 1 a raise, both
    either side. The model only chooses how far: a 4B model raised a lagging method (gate 05)."""
    kept, bare, against = {}, [], []
    for m, v in tilt.items():
        health = state.health.get(m, 1.0) if m in state.recorded else None
        down = bool(state.warnings.get(m)) or (health is not None and health < 1)
        up = bool(state.good.get(m)) or (health is not None and health > 1)
        if v == 1.0 or (v < 1 and down) or (v > 1 and up):
            kept[m] = v
            continue
        kept[m] = 1.0
        evident = bool(state.warnings.get(m) or state.good.get(m)) or health is not None
        (against if evident else bare).append(m)
    return kept, sorted(bare), sorted(against)


def _short(text: str, limit: int = MAX_REASON) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:max(0, limit - 3)].rstrip() + "..."


def _settled(reply: object, state: RegimeState, cap: float) -> tuple[dict[str, float], str, list[str], list[str]]:
    """The reply validated and grounded against the day's evidence, with the reason saying which
    moves were kept at x1 and why. The model's own words are cut to leave room for those notes,
    so the whole reason stays within MAX_REASON (gate 05 N1)."""
    tilt, reason = validated(reply, cap)
    tilt, bare, against = grounded(tilt, state)
    notes = ""
    if bare:
        notes += f" (kept at x1, no sign or record behind a move: {', '.join(bare)})"
    if against:
        notes += f" (kept at x1, a move against its evidence: {', '.join(against)})"
    return tilt, _short(reason, MAX_REASON - len(notes)) + notes, bare, against


def tilt_model(ctx: AppContext) -> tuple[ChatModel, str]:
    """The `[regime_ai_account] model` profile's model, else the research slot's, and a label
    naming both the profile and the model."""
    profile = ctx.config.regime_ai_account.model or slot_profile(ctx.config, "research")
    model = resolve_model(ctx, profile).model
    return model, f"{profile} ({model.name} {model.model})"


class DailyTilt:
    """The book's tilt on a regime-ai step. A day with a stored tilt answers from it; the step
    day without one asks the model and stores the answer, or the failure (no tilt); any other
    day has no tilt, so a book never asks the model about the past. `today` is the step day's
    stored tilt once the book has read that day."""

    def __init__(self, ctx: AppContext, account: str, day: date, cap: float, model: ChatModel | None = None) -> None:
        self._ctx, self._account, self._day, self._cap, self._model = ctx, account, day, cap, model
        self._repo = TiltRepo(ctx.conn)
        self._stored = self._repo.all(account)
        self.today: StoredTilt | None = None

    def __call__(self, state: RegimeState) -> dict[str, float]:
        found = self._stored.get(state.day)
        if found is None and state.day == self._day:
            found = self._stored[state.day] = self._ask(state)
            self._repo.save(found)
        if found is not None and state.day == self._day:
            self.today = found
        return dict(found.tilt) if found else {}

    def _ask(self, state: RegimeState) -> StoredTilt:
        label = None
        try:
            if self._model:
                model, label = self._model, f"{self._model.name} {self._model.model}"
            else:
                model, label = tilt_model(self._ctx)
            reply = _asked(model, tilt_messages(state, self._cap), tilt_schema())
            tilt, reason, _, _ = _settled(reply, state, self._cap)
            error = None
        except Exception as exc:
            tilt, reason, error = {}, None, str(exc) or type(exc).__name__
            log.warning("regime-ai tilt for %s: no tilt, the rules weights stand: %s", state.day, error)
        return StoredTilt(self._account, state.day, tilt, dict(state.weights), reason, error, label,
                          to_utc_iso(self._ctx.clock.utcnow()))


@dataclass
class ReplayStats:
    """What a replayed tilt did, for the evidence read: model calls against answers reused,
    days with no tilt and why, and how each method's kept moves and guard resets fell."""
    days: int = 0
    calls: int = 0
    cached: int = 0
    failed: int = 0
    skipped: int = 0
    """Days not asked: the run was stopped, or the model stopped answering (GIVE_UP_AFTER)."""
    moved: int = 0
    """Days with at least one kept move."""
    reset: int = 0
    """Days where a guard reset at least one move to x1."""
    up: Counter = field(default_factory=Counter)
    down: Counter = field(default_factory=Counter)
    bare: Counter = field(default_factory=Counter)
    against: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    gave_up: str | None = None

    def summary(self) -> str:
        def per(counts: Counter) -> str:
            return ", ".join(f"{m} {n}" for m, n in sorted(counts.items())) or "none"

        said = (f"Regime AI: {_count(self.days, 'day')} replayed with the model's tilt: "
                f"{_count(self.calls, 'model call')}, {self.cached} answered from the run's cache (same prompt), "
                f"{self.failed} with no tilt after a failure, {self.skipped} not asked. "
                f"{_count(self.moved, 'day')} with a kept move (raised: {per(self.up)}; cut: {per(self.down)}). "
                f"{_count(self.reset, 'day')} with a move reset to x1 (no sign or record: {per(self.bare)}; "
                f"against its evidence: {per(self.against)}).")
        if self.errors:
            said += " Failures: " + "; ".join(f"{e} ({n})" for e, n in self.errors.most_common(3)) + "."
        if self.gave_up:
            said += f" Stopped asking after {GIVE_UP_AFTER} days in a row without an answer: {self.gave_up}."
        return said


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


class ReplayTilt:
    """The regime-ai tilt asked for every past day from `start` to `end`, for a backtest: the
    live prompt (no date), validation, evidence and direction guards; a failure is no tilt that
    day. Answers are kept by exact prompt text for the run, so identical states (temperature 0)
    cost one call. Nothing is stored: a backtest only reads. Days before `start` get no tilt,
    so the replayed smoothing starts from the rules weights. `stop` ends the asking early (a
    cancelled job), skipping a pending retry too: the rest of the days replay on the rules
    weights in seconds. `busy` names other work that needs the model (a tick, research): the
    replay waits before each call while it runs, as the replay study does, so hours of replay
    never slow the live line's work (gate 06 #1)."""

    def __init__(self, model: ChatModel, cap: float, start: date, end: date,
                 stop: Callable[[], bool] | None = None, on_progress: Callable[[str], None] | None = None,
                 busy: Callable[[], str | None] | None = None) -> None:
        self._model, self._cap, self._start, self._end = model, cap, start, end
        self._stop, self._on_progress, self._busy = stop, on_progress, busy
        self._answers: dict[str, object] = {}
        self._silent = 0
        self.stats = ReplayStats()

    def __call__(self, state: RegimeState) -> dict[str, float]:
        if not self._start <= state.day <= self._end:
            return {}
        self.stats.days += 1
        try:
            return self._tilt(state)
        finally:
            if self._on_progress:
                self._on_progress(self._progress(state.day))

    def _tilt(self, state: RegimeState) -> dict[str, float]:
        s = self.stats
        if s.gave_up or self._stopped():
            s.skipped += 1
            return {}
        messages = tilt_messages(state, self._cap)
        key = json.dumps(messages, sort_keys=True)
        if key in self._answers:
            s.cached += 1
        else:
            self._yield(state.day)
            if self._stopped():
                s.skipped += 1
                return {}
            s.calls += 1
            try:
                self._answers[key] = _asked(self._model, messages, tilt_schema(), self._stopped)
                self._silent = 0
            except Stopped:
                s.skipped += 1
                return {}
            except Exception as exc:
                return self._failed(state, exc)
        try:
            tilt, _, bare, against = _settled(self._answers[key], state, self._cap)
        except Exception as exc:
            return self._failed(state, exc)
        moved = {m: v for m, v in tilt.items() if v != 1.0}
        s.up.update(m for m, v in moved.items() if v > 1)
        s.down.update(m for m, v in moved.items() if v < 1)
        s.bare.update(bare)
        s.against.update(against)
        s.moved += bool(moved)
        s.reset += bool(bare or against)
        return tilt

    def _stopped(self) -> bool:
        return bool(self._stop and self._stop())

    def _yield(self, day: date) -> None:
        while self._busy and not self._stopped() and (other := self._busy()):
            if self._on_progress:
                self._on_progress(f"{self._progress(day)}; waiting for {other} to finish")
            wait(YIELD_STEP_S)

    def _failed(self, state: RegimeState, exc: Exception) -> dict[str, float]:
        error = str(exc) or type(exc).__name__
        self.stats.failed += 1
        self.stats.errors[error[:120]] += 1
        log.warning("regime-ai replay tilt for %s: no tilt that day: %s", state.day, error)
        if _transient(exc) or (exc.__cause__ is not None and _transient(exc.__cause__)):
            self._silent += 1
            if self._silent >= GIVE_UP_AFTER:
                self.stats.gave_up = error[:120]
        else:
            self._silent = 0
        return {}

    def _progress(self, day: date) -> str:
        s = self.stats
        return (f"Regime AI tilt at {day.isoformat()} ({self._start.isoformat()} to {self._end.isoformat()}): "
                f"{_count(s.calls, 'model call')}, {s.cached} from cache, {s.failed} failed")


class Stopped(Exception):
    """The asker was stopped before a retry: nothing more is asked."""


def _asked(model: ChatModel, messages: list[dict], schema: dict,
           stopped: Callable[[], bool] | None = None) -> object:
    """The reply, asking once more after RETRY_AFTER_S when the first call got no answer at all,
    unless `stopped` says so by then (a Cancel must not sit through the wait and a second call).
    A bad answer is never asked again: that would let the line pick among the model's answers."""
    try:
        return _answer(model, messages, schema)
    except Exception as exc:
        if not _transient(exc):
            raise
        if stopped and stopped():
            raise Stopped from exc
        log.warning("regime-ai tilt: %s; asking once more in %.0f s", exc, RETRY_AFTER_S)
        wait(RETRY_AFTER_S)
        try:
            return _answer(model, messages, schema)
        except Exception as again:
            raise RuntimeError(f"failed twice, {RETRY_AFTER_S:.0f} s apart: {exc}; then {again}") from again


def _transient(exc: BaseException) -> bool:
    """A timeout or no connection: nothing was answered, so asking again selects nothing."""
    return isinstance(exc, httpx.TransportError) or isinstance(exc.__cause__, httpx.TransportError)


def _answer(model: ChatModel, messages: list[dict], schema: dict) -> object:
    """The model's reply, from synchronous code that may itself run inside an event loop (the
    tick's): the call gets a loop of its own on another thread."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, model.reply(messages, schema)).result()
