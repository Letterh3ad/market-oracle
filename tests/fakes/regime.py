"""A stand-in regime book with the shared contract's shape (state, lean_source, explain), so the
live account's plumbing is tested without the real blend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from oracle.broker.rules import Lean

METHODS = ("trend", "momentum", "reversion", "low_vol", "quality_value", "earnings_drift", "hold", "ai_verdict")


@dataclass(frozen=True)
class State:
    day: date
    mood: str
    flags: frozenset[str] = frozenset()
    readings: dict = field(default_factory=lambda: {"trend_gap": 0.04, "vol_pct": 0.3})
    base_weights: dict = field(default_factory=lambda: {m: 1 / len(METHODS) for m in METHODS})
    weights: dict = field(default_factory=lambda: {m: 1 / len(METHODS) for m in METHODS})
    warnings: dict = field(default_factory=dict)
    good: dict = field(default_factory=dict)
    health: dict = field(default_factory=lambda: dict.fromkeys(METHODS, 1.0))
    tilt: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Explained:
    lean: Lean
    parts: dict


class StubBook:
    """`leans` maps a listing to (label, probability, parts); a listing left out has no score.
    `mood` None reads no mood. Records the history and inputs it was built from."""

    def __init__(self, history, inputs, mood: str | None = "calm_up", leans: dict | None = None,
                 flags: frozenset[str] = frozenset()) -> None:
        self.history, self.inputs, self.mood, self.leans, self.flags = history, inputs, mood, leans or {}, flags
        self.asked: list[tuple[str, date]] = []

    def state(self, day: date) -> State | None:
        return State(day, self.mood, self.flags, warnings={"momentum": ("rebound",)}) if self.mood else None

    def lean_source(self):
        def lean(symbol, bars):
            self.asked.append((symbol, bars[-1].date))
            found = self.leans.get(symbol)
            return Lean(symbol, bars[-1].date, found[0], found[1], "regime") if found else None
        return lean

    def explain(self, symbol: str, day: date) -> Explained | None:
        found = self.leans.get(symbol)
        return Explained(Lean(symbol, day, found[0], found[1], "regime"), found[2]) if found else None


def use_stub(monkeypatch, **kwargs) -> list[StubBook]:
    """Every step builds a StubBook with `kwargs`; returns the books built, newest last."""
    built: list[StubBook] = []

    def build(ctx, history, inputs):
        built.append(StubBook(history, inputs, **kwargs))
        return built[-1]

    monkeypatch.setattr("oracle.services.regime_service.build_book", build)
    return built
