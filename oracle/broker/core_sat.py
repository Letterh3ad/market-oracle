"""Core-satellite: the core, an equal-weight buy-and-hold of the main list bought once and never
rebalanced, and the satellite, traded by the regime blend under the strategy's rules within its own
cash. The satellite's share is 0 until a signal earns weight (next-signals spec 9, 10), so the core
holds it all. The planning is shared by the replayed line and the live account.

A rebalance (only with `rebalance_years` set, which no line does now) is two steps: the day it falls
due, sells trim the overweights and drop listings no longer in the core; once they have filled, buys
top the underweights up from the core's cash only. With nothing to trim (the first day) the buys go
the same day."""

from __future__ import annotations

import calendar
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from oracle.analysts.risk import Priced
from oracle.broker.backtest import (LeanCap, LeanSource, Replay, Trade, Venue, _charges, _price, dividend_cash, fill_costs,
                                    replay)
from oracle.broker.dividends import DividendBook, Payout
from oracle.broker.fills import buy_fill, sell_fill
from oracle.broker.orders import Action, Side
from oracle.broker.rules import Decision, Rules

SATELLITE_SHARE = 0.0
"""The satellite's share of the starting capital; the core has the rest. 0 until a signal passes
the confirm and then its own six months of paper trading (spec 10), and never over SATELLITE_CAP."""
SATELLITE_CAP = 0.20
SATELLITE_ZERO = ("satellite weight 0: no signal has passed the confirm test and then six months as its own paper "
                  "line, so the satellite trades nothing (next-signals spec 9, 10)")
"""Why core-sat's satellite holds no money: its caption, card, and each waiting buy it drops."""
SATELLITE_EXIT = ("satellite weight 0: no signal has passed the confirm test and then six months as its own paper "
                  "line, so the satellite sells what it holds and buys nothing (next-signals spec 9, 10)")
"""The reason on the satellite's sells while its weight is 0."""
REBALANCE_YEARS: int | None = None
"""None: the core is bought once and held, never set back to equal weight (spec 9)."""
REBALANCE = "core"
"""How every core order's reason starts."""
STALE = timedelta(days=7)
"""A holding or member whose last close is older than this on a rebalance day is left out of it:
not counted, trimmed, sold or bought (suspended, delisted or no longer fetched)."""
EXPIRED = f"expired, no fill within {STALE.days} days"
"""A core order still waiting this long is dropped, so a listing that stopped trading never blocks
a later rebalance."""


@dataclass(frozen=True)
class CoreOrder:
    symbol: str
    side: Side
    cash: float | None
    """A buy's amount, base currency."""
    shares: int | None
    """A sell's whole shares."""
    reason: str


@dataclass(frozen=True)
class CoreSat:
    equity: tuple[tuple[date, float], ...]
    """Core plus satellite at each day's close, base currency."""
    core: Replay
    satellite: Replay

    @property
    def trades(self) -> tuple[Trade, ...]:
        return tuple(sorted(self.core.trades + self.satellite.trades, key=lambda t: (t.day, t.symbol, t.side)))

    @property
    def rejected(self) -> tuple[str, ...]:
        return self.core.rejected + self.satellite.rejected

    @property
    def unfilled(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.core.unfilled) | set(self.satellite.unfilled)))


def rebalance_days(days: Sequence[date], years: int | None = REBALANCE_YEARS) -> list[date]:
    """The first of `days` (sorted market days), and with `years` the first on or after each
    anniversary every `years` years of it."""
    if not days:
        return []
    out, k = [days[0]], 1
    if years is None:
        return out
    for day in days:
        if day >= add_years(days[0], k * years):
            out.append(day)
            k += 1
    return out


def add_years(day: date, years: int) -> date:
    year = day.year + years
    return day.replace(year=year, day=min(day.day, calendar.monthrange(year, day.month)[1]))


def rebalance_sells(members: Collection[str], held: Mapping[str, tuple[int, float]], cash: float) -> list[CoreOrder]:
    """`held` is each holding's (shares, value in base) at the last close; `members` the listings
    the core holds equally, each with a current price. A holding not a member goes; one over its
    equal share is trimmed by whole shares."""
    target = _target(members, held, cash)
    out = []
    for symbol, (shares, value) in sorted(held.items()):
        if shares <= 0:
            continue
        if symbol not in members:
            out.append(CoreOrder(symbol, Side.SELL, None, shares, f"{REBALANCE}: no longer in the core"))
        elif value > target and (trim := int((value - target) / (value / shares))) > 0:
            out.append(CoreOrder(symbol, Side.SELL, None, trim, f"{REBALANCE}: trimmed back to an equal share"))
    return out


def rebalance_buys(members: Collection[str], held: Mapping[str, tuple[int, float]], cash: float) -> list[CoreOrder]:
    """Each member under its equal share topped up towards it; together never more than `cash`,
    each scaled down alike when the cash is short."""
    target = _target(members, held, cash)
    wanted = {s: target - held.get(s, (0, 0.0))[1] for s in sorted(members)}
    wanted = {s: v for s, v in wanted.items() if v > 0}
    total = sum(wanted.values())
    if total <= 0 or cash <= 0:
        return []
    scale = min(1.0, cash / total)
    return [CoreOrder(s, Side.BUY, v * scale, None, f"{REBALANCE}: bought towards an equal share")
            for s, v in wanted.items()]


def expired(placed: date, day: date) -> bool:
    return day - placed > STALE


def whole_share_buys(orders: list[CoreOrder], prices: Mapping[str, float]) -> list[CoreOrder]:
    """The buys worth at least one share at its last close: a smaller top-up could only be rejected."""
    return [o for o in orders if o.side != Side.BUY or o.cash >= prices[o.symbol]]


def _target(members: Collection[str], held: Mapping[str, tuple[int, float]], cash: float) -> float:
    equity = cash + sum(v for _, v in held.values())
    return equity / len(members) if members else 0.0


def core_replay(history: Mapping[str, Sequence[Priced]], start: date, end: date, *, capital: float,
                members: Collection[str], slippage: float, commission: float,
                fill_at: Literal["open", "close"] = "open", venues: Mapping[str, Venue] | None = None,
                rebalance_years: int | None = REBALANCE_YEARS,
                dividends: Mapping[str, Sequence[Payout]] | None = None) -> Replay:
    """The core alone over `history` (as `backtest.replay` takes it): `members` bought in equal
    amounts, set back to equal weight on each `rebalance_days` day (the first only, unless
    `rebalance_years`), filling like the traded lines (the next bar's open or close, with the same
    costs). Decisions on a day read only its bars. `dividends` are paid as `backtest.replay` pays them."""
    days = sorted({p.bar.date for rows in history.values() for p in rows if start <= p.bar.date <= end})
    due = set(rebalance_days(days, rebalance_years))
    seen = dict.fromkeys(history, 0)
    last: dict[str, tuple[date, float]] = {}
    held: dict[str, int] = {}
    cash = capital
    pending: list[tuple[date, CoreOrder]] = []
    buying_next = False
    equity, trades, orders, rejected = [], [], [], []
    book, rates, paid = DividendBook(dividends), {}, 0.0
    for day in days:
        today = {}
        for s, rows in history.items():
            while seen[s] < len(rows) and rows[seen[s]].bar.date <= day:
                seen[s] += 1
            if seen[s] and rows[seen[s] - 1].bar.date == day:
                today[s] = rows[seen[s] - 1]
            if seen[s] and rows[seen[s] - 1].rate:
                rates[s] = rows[seen[s] - 1].rate
        got = dividend_cash(book, day, rates, venues)
        cash, paid = cash + got, paid + got
        waiting = []
        for placed, order in sorted(pending, key=lambda x: x[1].side != Side.SELL):
            p = today.get(order.symbol)
            if p is None or _price(p.bar, fill_at) is None or p.rate is None:
                if expired(placed, day):
                    rejected.append(f"{day.isoformat()} {order.symbol}: {EXPIRED}")
                else:
                    waiting.append((placed, order))
                continue
            trade = _fill(order, p, _price(p.bar, fill_at), cash, held,
                          _charges(order.symbol, p, venues, slippage, commission))
            if trade is None:
                rejected.append(f"{day.isoformat()} {order.symbol}: the core's buy buys no whole share after costs")
                continue
            cash += trade.cash
            change = trade.shares if trade.side == Side.BUY else -trade.shares
            book.fill(trade.symbol, trade.day, change)
            held[trade.symbol] = held.get(trade.symbol, 0) + change
            if held[trade.symbol] <= 0:
                held.pop(trade.symbol)
            trades.append(trade)
        pending = waiting
        for s, p in today.items():
            if p.rate:
                last[s] = (day, p.bar.close / p.rate)
        equity.append((day, cash + sum(n * last[s][1] for s, n in held.items() if s in last)))
        if day == days[-1]:
            continue
        fresh = {s: close for s, (on, close) in last.items() if day - on <= STALE}
        priced = [s for s in members if s in fresh]
        values = {s: (n, n * fresh[s]) for s, n in held.items() if s in fresh}
        placed: list[CoreOrder] = []
        if day in due:
            placed = rebalance_sells(priced, values, cash)
            buying_next = bool(placed)
            if not placed:
                placed = whole_share_buys(rebalance_buys(priced, values, cash), fresh)
        elif buying_next and not pending:
            buying_next = False
            placed = whole_share_buys(rebalance_buys(priced, values, cash), fresh)
        for order in placed:
            pending.append((day, order))
            orders.append((day, Decision(order.symbol, Action.BUY if order.side == Side.BUY else Action.SELL,
                                         order.cash, order.reason)))
    return Replay(tuple(equity), tuple(trades), tuple(orders), tuple(rejected),
                  tuple(sorted({o.symbol for _, o in pending})), paid)


def _fill(order: CoreOrder, p: Priced, price: float, cash: float, held: Mapping[str, int], charges) -> Trade | None:
    costs, venue = charges
    if order.side == Side.SELL:
        fill = sell_fill(min(order.shares, held.get(order.symbol, 0)), open_price=price, rate=p.rate,
                         country=venue.country, costs=costs)
    else:
        fill = buy_fill(min(order.cash, cash), open_price=price, rate=p.rate, country=venue.country,
                        exchange=venue.exchange, costs=costs)
        if fill.shares == 0:
            return None
    return Trade(p.bar.date, order.symbol, order.side, fill.shares, fill.price, p.bar.currency, fill.cash,
                 fill_costs(fill, p.rate), order.reason)


def core_sat_replay(history: Mapping[str, Sequence[Priced]], start: date, end: date, *, rules: Rules,
                    risk: Mapping[str, float], members: Collection[str], slippage: float, commission: float,
                    lean: LeanSource, cap: LeanCap | None = None, fill_at: Literal["open", "close"] = "open",
                    venues: Mapping[str, Venue] | None = None, satellite_share: float = SATELLITE_SHARE,
                    rebalance_years: int | None = REBALANCE_YEARS,
                    dividends: Mapping[str, Sequence[Payout]] | None = None) -> CoreSat:
    """`risk["account"]` split `satellite_share` to the satellite, the rest to the core: `lean`
    (the regime book's, with any extra methods its inputs carry) traded under `rules` within the
    satellite's own cash, its risk sizes and cash floor a share of the satellite alone. The two
    never share cash. At share 0 the satellite trades nothing and the core holds it all."""
    capital = risk["account"]
    c = core_replay(history, start, end, capital=capital * (1 - satellite_share), members=members,
                    slippage=slippage, commission=commission, fill_at=fill_at, venues=venues,
                    rebalance_years=rebalance_years, dividends=dividends)
    s = (replay(history, start, end, rules=rules, risk={**risk, "account": capital * satellite_share},
                slippage=slippage, commission=commission, lean=lean, fill_at=fill_at, venues=venues, cap=cap,
                dividends=dividends)
         if satellite_share > 0 else Replay(tuple((d, 0.0) for d, _ in c.equity), (), (), (), ()))
    sat = dict(s.equity)
    return CoreSat(tuple((d, v + sat[d]) for d, v in c.equity), c, s)
