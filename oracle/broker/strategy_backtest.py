"""The strategy test's backtest engine (research-lab spec 6, 7): a rule's target weights, decided at
a close from what is known then, fill at the next session's open after UK retail costs; the
platform fee comes off the pot at each month end. Values are in one currency (the pot's); a
listing's currency only decides its FX fee. Numbers in, numbers out."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.broker.trend_overlay import Session, _cash
from oracle.broker.uk_costs import Listing, TradeCost, UkCosts, trade_cost

__all__ = ["Decide", "StrategyRun", "Trade", "Weights", "simulate"]

Weights = Mapping[str, float]
"""Share of the pot per symbol; the rest is cash."""
Decide = Callable[[date], Weights | None]
"""Called after each close but the window's last: the target from the next open, or None to keep."""
_DUST = 1e-9


@dataclass(frozen=True)
class Trade:
    day: date
    symbol: str
    side: str
    value: float
    """What changed hands, costs excluded."""
    cost: TradeCost


@dataclass(frozen=True)
class StrategyRun:
    equity: list[tuple[date, float]]
    """The pot at each close inside the window."""
    trades: tuple[Trade, ...]
    platform: float
    """Platform fees taken."""

    @property
    def traded(self) -> float:
        return sum(t.value for t in self.trades)

    @property
    def costs(self) -> TradeCost:
        """Trading costs by kind."""
        return TradeCost(*(sum(getattr(t.cost, k) for t in self.trades) for k in ("dealing", "spread", "stamp", "fx")))

    @property
    def turnover(self) -> float:
        """Yearly one-way turnover: value bought and sold over two, a year, over the mean pot."""
        if len(self.equity) < 2:
            return 0.0
        years = (self.equity[-1][0] - self.equity[0][0]).days / 365.25
        mean = sum(v for _, v in self.equity) / len(self.equity)
        return self.traded / 2 / years / mean if years > 0 and mean > 0 else 0.0


def simulate(days: Sequence[date], prices: Mapping[str, Mapping[date, Session]], listings: Mapping[str, Listing],
             decide: Decide, start: date, end: date, costs: UkCosts, pot: float, initial: Weights | None = None,
             cash_yield: Mapping[date, float] | None = None) -> StrategyRun:
    """The rule over the calendar `days` from `start` to `end`, from `pot` in cash (or held as
    `initial` at the first close, at no cost). A target fills at each symbol's open (its close when
    the open is missing), sells first, then buys from the cash they leave, each buy's costs paid
    out of the money it commits. A symbol without a session that day (a London bank holiday on a
    US calendar) keeps its holding and fills its target at its next session, unless a newer target
    comes first. Cash earns `cash_yield` (annual percent by day, the latest known at the previous
    close) on days it trades nothing, as the trend overlay's own simulator does."""
    window = [d for d in days if start <= d <= end]
    if not window:
        return StrategyRun([], (), 0.0)
    last: dict[str, float] = {}

    def close(s: str, d: date) -> float | None:
        bar = prices.get(s, {}).get(d)
        if bar is not None and bar.close > 0:
            last[s] = bar.close
        return last.get(s)

    first = window[0]
    for s in prices:
        close(s, first)
    shares: dict[str, float] = {}
    cash = pot
    for s, w in (initial or {}).items():
        if w > 0 and last.get(s):
            shares[s] = pot * w / last[s]
            cash -= pot * w
    yields = sorted((cash_yield or {}).items())
    month_ends = {a for a, b in zip(days, days[1:]) if (a.year, a.month) != (b.year, b.month)}
    trades: list[Trade] = []
    platform = 0.0
    pending: dict[str, float] = {}
    """Target weight per symbol not yet filled."""

    def aim(target: Weights | None) -> None:
        if target is not None:
            pending.clear()
            pending.update({s: target.get(s, 0.0) for s in set(target) | set(shares)})

    aim(decide(first) if len(window) > 1 else None)
    equity = [(first, cash + sum(n * last[s] for s, n in shares.items()))]
    for prev, d in zip(window, window[1:]):
        before = len(trades)
        if pending:
            cash = _fill(d, pending, prices, listings, costs, shares, cash, last, trades)
        if yields and len(trades) == before:
            cash *= 1 + _cash(yields, prev)
        value = cash + sum(n * (close(s, d) or 0.0) for s, n in shares.items())
        for s in prices:
            close(s, d)
        if d != window[-1] and d in month_ends:
            fee = min(costs.fees.platform(value), value)
            if value > 0 and fee > 0:
                keep = 1 - fee / value
                cash *= keep
                shares = {s: n * keep for s, n in shares.items()}
                platform += fee
                value -= fee
        equity.append((d, value))
        if d != window[-1]:
            aim(decide(d))
    return StrategyRun(equity, tuple(trades), platform)


def _fill(d: date, pending: dict[str, float], prices, listings, costs: UkCosts, shares: dict[str, float],
          cash: float, last: dict[str, float], trades: list[Trade]) -> float:
    """Trades each pending symbol with a session on `d` towards its weight and drops it from `pending`."""
    fills = {}
    for s in set(pending) | set(shares):
        bar = prices.get(s, {}).get(d)
        if bar is not None and bar.close > 0:
            fills[s] = bar.open if bar.open and bar.open > 0 else bar.close
    held = {s: n * fills.get(s, last.get(s, 0.0)) for s, n in shares.items()}
    pot = cash + sum(held.values())
    wants = {s: pending.pop(s) * pot - held.get(s, 0.0) for s in fills if s in pending}
    for s, want in sorted(wants.items()):
        sell = min(-want, held.get(s, 0.0))
        if sell <= _DUST * pot:
            continue
        cost = trade_cost(costs, listings[s], "sell", sell)
        if cost.total >= sell:
            continue
        shares[s] -= sell / fills[s]
        cash += sell - cost.total
        trades.append(Trade(d, s, "sell", sell, cost))
    buys = {s: w for s, w in wants.items() if w > _DUST * pot}
    scale = min(1.0, cash / sum(buys.values())) if buys else 0.0
    for s, want in sorted(buys.items()):
        spend = want * scale
        value = (spend - costs.fees.dealing) / (1 + costs.rate(listings[s], "buy"))
        if value <= 0:
            continue
        cost = trade_cost(costs, listings[s], "buy", value)
        shares[s] = shares.get(s, 0.0) + value / fills[s]
        cash -= value + cost.total
        trades.append(Trade(d, s, "buy", value, cost))
    return max(cash, 0.0)
