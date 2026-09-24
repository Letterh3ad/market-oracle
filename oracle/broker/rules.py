"""The strategy account's fixed rules: leans in, orders out. Pure, so every rule is tested
on its own and a decision can be replayed from its inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from oracle.broker.orders import Action

LABELS = ("strong sell", "sell", "hold", "buy", "strong buy")
BUY_LABELS = frozenset({"buy", "strong buy"})
SELL_LABELS = frozenset({"sell", "strong sell"})
PENDING = "an order is already pending"


@dataclass(frozen=True)
class Lean:
    symbol: str
    as_of: date
    label: str
    """One of LABELS."""
    probability: float
    source: str
    """Who made it: `baseline` until AI leans exist."""


@dataclass(frozen=True)
class Position:
    symbol: str
    shares: int
    cost: float
    """What the shares cost in the base currency, costs included (average cost basis)."""
    value: float | None
    """At the last stored close, in the base currency; None without a rate, so no stop can
    be judged."""


@dataclass(frozen=True)
class Rules:
    min_probability: float
    max_position: float
    """Share of equity."""
    max_positions: int
    cash_floor: float
    """Share of equity always kept in cash."""
    stop_loss: float
    """Sell when a position is this far below its cost."""


@dataclass(frozen=True)
class Decision:
    symbol: str
    action: Action
    cash: float | None
    """For a buy, the base-currency amount."""
    reason: str
    needs_research: bool = False
    """The listing has no verdict yet, or it is too old: researching it would let the AI account act."""


def decide(leans: Mapping[str, Lean], holdings: Mapping[str, Position], cash: float, equity: float,
           sizes: Mapping[str, float], rules: Rules, pending: set[str]) -> list[Decision]:
    """Sells first (stops, then sell leans), then buys by probability. `sizes` is the risk
    analyst's suggested share of equity per listing; sell proceeds are not counted until
    they fill. `cash` excludes what pending buys will spend, and a pending buy takes a
    position slot."""
    out = [Decision(s, Action.SKIP, None, PENDING) for s in sorted(pending)]
    for symbol, h in sorted(holdings.items()):
        if symbol in pending:
            continue
        lean = leans.get(symbol)
        if h.value is not None and h.value <= h.cost * (1 - rules.stop_loss):
            out.append(Decision(symbol, Action.SELL, None, f"stop: {1 - h.value / h.cost:.1%} below cost, "
                                                      f"past the {rules.stop_loss:.0%} limit"))
        elif lean is not None and lean.label in SELL_LABELS:
            out.append(Decision(symbol, Action.SELL, None, f"{lean.source} lean is {lean.label}"))
    positions = len(holdings.keys() | pending)
    spendable = cash - rules.cash_floor * equity
    wanted = [x for x in leans.values() if x.label in BUY_LABELS and x.symbol not in holdings
              and x.symbol not in pending]
    for lean in sorted(wanted, key=lambda x: (-x.probability, x.symbol)):
        skip = _why_not(lean, positions, spendable, sizes, rules)
        if skip:
            out.append(Decision(lean.symbol, Action.SKIP, None, skip))
            continue
        amount = min(sizes[lean.symbol], rules.max_position) * equity
        amount = min(amount, spendable)
        spendable -= amount
        positions += 1
        out.append(Decision(lean.symbol, Action.BUY, amount, f"{lean.source} lean {lean.label} at {lean.probability:.0%}"))
    return out


def _why_not(lean: Lean, positions: int, spendable: float, sizes: Mapping[str, float], rules: Rules) -> str | None:
    if lean.probability < rules.min_probability:
        return f"probability {lean.probability:.0%} is under the {rules.min_probability:.0%} threshold"
    if positions >= rules.max_positions:
        return f"already {rules.max_positions} positions"
    if sizes.get(lean.symbol, 0.0) <= 0:
        return "no risk size yet: analyse it first"
    if spendable <= 0:
        return "no cash above the cash floor"
    return None
