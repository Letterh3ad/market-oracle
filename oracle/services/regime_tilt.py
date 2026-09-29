"""The regime-ai line's daily tilt. Once per step day a model (local by default) reads the day's
regime state and multiplies each method's rules weight within the cap. Each day's answer, or
why there was none, is stored: a restart or a second step that day reuses it, and every later
book replays the tilts actually applied instead of asking the model about the past."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from oracle.broker.regime import METHODS, SIGNS, RegimeState
from oracle.research.llm import ChatModel
from oracle.services.context import AppContext
from oracle.services.models_service import resolve_model, slot_profile
from oracle.store.db import to_utc_iso
from oracle.store.regime import StoredTilt, TiltRepo

__all__ = ["MAX_REASON", "SYSTEM", "BadTilt", "DailyTilt", "tilt_messages", "tilt_model", "tilt_schema", "validated"]

log = logging.getLogger(__name__)

MAX_REASON = 300
"""Characters of the model's reason kept: the card shows one or two sentences."""

# Every word the model reads is here, so the prompt is changed in one place.
SYSTEM = (
    "You tune a paper-trading blend of stock-picking methods for one day. Each method has a weight from "
    "fixed rules: its preset for the market mood, times its playbook signs and its health. Multiply each "
    "method's weight by a number from {low} to {high}; 1 keeps the rules weight. Move a weight only for a "
    "reason in the data (a warning, a good sign, health far from 1, the mood and readings); otherwise answer 1. "
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
                "hold": "buy and hold", "ai_verdict": "research verdicts"}
HEALTH = "Health is a method's own recent picks against the market: 0.5 lagging, 1 neutral, 1.5 leading."
SIGN_INFO = {(s.method, s.name): s for s in SIGNS}


class BadTilt(ValueError):
    """The model answered, but not with a usable tilt."""


def tilt_messages(state: RegimeState, cap: float) -> list[dict]:
    """The day's state as a short prompt; the same state always gives the same text."""
    flags = ", ".join(f"{f} ({FLAGS.get(f, f)})" for f in sorted(state.flags)) or "none"
    readings = ", ".join(f"{k} {v:.3f} ({READINGS.get(k, k)})" for k, v in sorted(state.readings.items())
                         if isinstance(v, (int, float)))
    lines = [f"Day: {state.day.isoformat()}", f"Mood: {state.mood} ({MOODS.get(state.mood, state.mood)})",
             f"Flags: {flags}", f"Readings: {readings}", HEALTH, "Methods:"]
    lines += [_method(state, m) for m in METHODS]
    return [{"role": "system", "content": SYSTEM.format(low=_num(1 - cap), high=_num(1 + cap))},
            {"role": "user", "content": "\n".join(lines)}]


def _method(state: RegimeState, m: str) -> str:
    signs = [(name, True) for name in state.warnings.get(m, ())] + [(name, False) for name in state.good.get(m, ())]
    said = "; ".join(_sign(m, name, warning) for name, warning in signs) or "no signs"
    return (f"- {m} ({METHOD_NAMES.get(m, m)}): preset {state.base_weights.get(m, 0):.3f}, "
            f"rules weight {state.weights.get(m, 0):.3f}, health {state.health.get(m, 1.0):.2f}; {said}")


def _sign(method: str, name: str, warning: bool) -> str:
    sign = SIGN_INFO.get((method, name))
    what = f"{'warning' if warning else 'good sign'} {name}"
    return f"{what}: {sign.reason} ({sign.source})" if sign else what


def _num(v: float) -> str:
    return f"{v:g}"


def tilt_schema() -> dict:
    return {"type": "object", "properties": {**{m: {"type": "number"} for m in METHODS}, "reason": {"type": "string"}},
            "required": [*METHODS, "reason"]}


def validated(reply: object, cap: float) -> tuple[dict[str, float], str]:
    """Known methods' finite numbers clipped to [1 - cap, 1 + cap], and the reason cut short;
    BadTilt when no known method has a number or there is no reason."""
    if not isinstance(reply, Mapping):
        raise BadTilt(f"the model's answer was not a JSON object: {str(reply)[:80]!r}")
    reason = reply.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise BadTilt("the model gave no reason")
    tilt = {m: round(min(1 + cap, max(1 - cap, float(v))), 4) for m, v in reply.items()
            if m in METHODS and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    if not tilt:
        raise BadTilt("the model gave no multiplier for any known method")
    return tilt, _short(reason)


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_REASON else text[:MAX_REASON - 3].rstrip() + "..."


def tilt_model(ctx: AppContext) -> tuple[ChatModel, str]:
    """The `[regime_ai_account] model` profile's model, else the research slot's, and a label
    naming both the profile and the model."""
    profile = ctx.config.regime_ai_account.model or slot_profile(ctx.config, "research")
    model = resolve_model(ctx, profile).model
    return model, f"{profile} ({model.name} {model.model})"


class DailyTilt:
    """The book's tilt on a regime-ai step. A day with a stored tilt answers from it; the step
    day without one asks the model once and stores the answer, or the failure (no tilt); any
    other day has no tilt, so a book never asks the model about the past. `today` is the step
    day's stored tilt once the book has read that day."""

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
            reply = _answer(model, tilt_messages(state, self._cap), tilt_schema())
            tilt, reason = validated(reply, self._cap)
            error = None
        except Exception as exc:
            tilt, reason, error = {}, None, str(exc) or type(exc).__name__
            log.warning("regime-ai tilt for %s: no tilt, the rules weights stand: %s", state.day, error)
        return StoredTilt(self._account, state.day, tilt, dict(state.weights), reason, error, label,
                          to_utc_iso(self._ctx.clock.utcnow()))


def _answer(model: ChatModel, messages: list[dict], schema: dict) -> object:
    """The model's reply, from synchronous code that may itself run inside an event loop (the
    tick's): the call gets a loop of its own on another thread."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, model.reply(messages, schema)).result()
