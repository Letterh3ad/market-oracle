"""A stand-in regime book with the real one's shape (state, explain, cap), so the live account's
plumbing is tested with leans chosen by the test."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from oracle.broker.regime import METHODS, Explained
from oracle.broker.rules import Lean


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


class StubBook:
    """`leans` maps a listing to (label, probability, parts); a listing left out has no score.
    `mood` None reads no mood. `capped` listings lean hold after the cap. Records the history and
    inputs it was built from, each `explain` asked and each cap's holdings."""

    def __init__(self, history, inputs, mood: str | None = "calm_up", leans: dict | None = None,
                 flags: frozenset[str] = frozenset(), capped: frozenset[str] = frozenset()) -> None:
        self.history, self.inputs, self.mood, self.leans, self.flags = history, inputs, mood, leans or {}, flags
        self.capped = capped
        self.asked: list[tuple[str, date]] = []
        self.held: list[set[str]] = []

    def state(self, day: date) -> State | None:
        return State(day, self.mood, self.flags, warnings={"momentum": ("rebound",)}) if self.mood else None

    def explain(self, symbol: str, day: date) -> Explained | None:
        self.asked.append((symbol, day))
        found = self.leans.get(symbol)
        if not found:
            return None
        return Explained(Lean(symbol, day, found[0], found[1], "regime"), found[2], sum(found[2].values()), {},
                         self.state(day))

    def cap(self, leans, held):
        self.held.append(set(held))
        return {s: Lean(s, x.as_of, "hold", 0.5, x.source) if s in self.capped else x for s, x in leans.items()}


def use_stub(monkeypatch, **kwargs) -> list[StubBook]:
    """Every step builds a StubBook with `kwargs`; returns the books built, newest last."""
    built: list[StubBook] = []

    def build(ctx, history, inputs):
        built.append(StubBook(history, inputs, **kwargs))
        return built[-1]

    monkeypatch.setattr("oracle.services.regime_service.build_book", build)
    return built
