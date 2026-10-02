"""Cash dividends owed to a holder: the one rule the paper ledgers and the backtest engines share.
A dividend is owed for the shares held at the close before its ex-date and paid as cash on its
pay date, or, when no source gives one, an assumed lag after the ex-date. From the ex-date to the
pay day it is a receivable: counted in equity (amount and shares are known, so no look-ahead), but
not spendable. Numbers in, numbers out."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

LAGS = {"USD": 30, "CAD": 30, "GBP": 45, "EUR": 45, "CHF": 45, "AUD": 45, "JPY": 95, "KRW": 130}
"""Days from ex-date to an assumed pay date, by the dividend's currency: a typical upper bound
for that market, so a credit is late rather than early (US and Canadian payers mostly pay within
a month; London, euro and Swiss within six weeks; Japan's year-end about 85 days after; a
Korean year-end in April). Others take DEFAULT_LAG."""
DEFAULT_LAG = 90


def assumed_lag(currency: str) -> timedelta:
    return timedelta(days=LAGS.get(currency, DEFAULT_LAG))


@dataclass(frozen=True)
class Payout:
    ex_date: date
    amount: float
    """Per share, in the listing's currency (major unit)."""
    currency: str
    pay_date: date | None = None

    @property
    def pay_day(self) -> date:
        """When it is paid: the known pay date, else the assumed one; never before the ex-date."""
        return max(self.pay_date or self.ex_date + assumed_lag(self.currency), self.ex_date)


@dataclass(frozen=True)
class Credit:
    payout: Payout
    shares: int
    """Held at the close before the ex-date."""

    @property
    def cash(self) -> float:
        """In the listing's currency."""
        return self.shares * self.payout.amount


Changes = Sequence[tuple[date, int]]
"""(day the shares changed, by how many), a fill on that day counting from that day's open."""


def credits(changes: Changes, payouts: Sequence[Payout], upto: date) -> list[Credit]:
    """Every payout paid by `upto` with shares held at the close before its ex-date."""
    return _held(changes, [p for p in payouts if p.pay_day <= upto])


def owed(changes: Changes, payouts: Sequence[Payout], day: date) -> list[Credit]:
    """Every payout gone ex by `day` but not paid by then: the receivable on `day`."""
    return _held(changes, [p for p in payouts if p.ex_date <= day < p.pay_day])


def _held(changes: Changes, payouts: Sequence[Payout]) -> list[Credit]:
    out = []
    for p in payouts:
        shares = sum(n for d, n in changes if d < p.ex_date)
        if shares > 0:
            out.append(Credit(p, shares))
    return out


class DividendBook:
    """For a day-by-day engine: record each fill, then ask on each day which payouts are now
    paid (`due`, each once) and which are owed but not yet paid (`owed`, for equity)."""

    def __init__(self, payouts: Mapping[str, Sequence[Payout]] | None) -> None:
        self._waiting = {s: sorted(ps, key=lambda p: p.pay_day) for s, ps in (payouts or {}).items() if ps}
        self._changes: dict[str, list[tuple[date, int]]] = {}

    def fill(self, symbol: str, day: date, shares: int) -> None:
        """`shares` bought (positive) or sold (negative) at `day`'s fill."""
        self._changes.setdefault(symbol, []).append((day, shares))

    def due(self, day: date) -> list[tuple[str, Credit]]:
        out = []
        for s, waiting in self._waiting.items():
            paid = 0
            while paid < len(waiting) and waiting[paid].pay_day <= day:
                paid += 1
            if paid:
                out += [(s, c) for c in credits(self._changes.get(s, ()), waiting[:paid], day)]
                del waiting[:paid]
        return out

    def owed(self, day: date) -> list[tuple[str, Credit]]:
        return [(s, c) for s, waiting in self._waiting.items() for c in owed(self._changes.get(s, ()), waiting, day)]
