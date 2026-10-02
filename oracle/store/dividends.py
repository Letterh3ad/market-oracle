from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.market.dividends import Dividend
from oracle.store.db import transaction


def _day(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


class DividendRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, symbol: str, dividends: Sequence[Dividend], source: str, fetched_at: str) -> None:
        """Store or refresh each ex-date's amount; a pay date already found is kept."""
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT INTO dividends (symbol, ex_date, amount, currency, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (symbol, ex_date) DO UPDATE SET amount = excluded.amount, currency = excluded.currency, "
                "source = excluded.source, fetched_at = excluded.fetched_at",
                [(symbol, d.ex_date.isoformat(), d.amount, d.currency, source, fetched_at) for d in dividends])

    def replace(self, symbol: str, dividends: Sequence[Dividend], first: date, last: date, source: str,
                fetched_at: str) -> None:
        """A fetch is the whole truth for its window: store its dividends and drop the stored ones
        with an ex-date after `first` to `last` it no longer lists (moved or cancelled), so none
        counts twice. `first` itself is kept: the window opens at midnight UTC, so a market east of
        UTC can stamp that day's dividend before it. A paper ledger's credit of a dropped one stays
        in its history."""
        kept = {d.ex_date.isoformat() for d in dividends}
        with transaction(self._conn):
            stale = [e for (e,) in self._conn.execute(
                "SELECT ex_date FROM dividends WHERE symbol = ? AND ex_date > ? AND ex_date <= ?",
                (symbol, first.isoformat(), last.isoformat())) if e not in kept]
            self._conn.executemany("DELETE FROM dividends WHERE symbol = ? AND ex_date = ?",
                                   [(symbol, e) for e in stale])
            self.upsert(symbol, dividends, source, fetched_at)

    def history(self, symbol: str, start: date | None = None, end: date | None = None) -> list[Dividend]:
        """Oldest first, ex-dates from `start` to `end` (both included)."""
        rows = self._conn.execute(
            "SELECT ex_date, amount, currency, pay_date FROM dividends WHERE symbol = ? AND ex_date BETWEEN ? AND ? "
            "ORDER BY ex_date", (symbol, (start or date.min).isoformat(), (end or date.max).isoformat()))
        return [Dividend(date.fromisoformat(e), a, c, _day(p)) for e, a, c, p in rows]

    def set_pay_dates(self, symbol: str, pay_dates: Mapping[date, date]) -> int:
        """Pay dates by ex-date, for the stored dividends among them; how many were set."""
        with transaction(self._conn):
            return sum(self._conn.execute(
                "UPDATE dividends SET pay_date = ? WHERE symbol = ? AND ex_date = ?",
                (pay.isoformat(), symbol, ex.isoformat())).rowcount for ex, pay in pay_dates.items())

    def span(self, symbol: str) -> tuple[date, date] | None:
        """The days dividends were asked for, first and last; None when never asked."""
        row = self._conn.execute("SELECT first, last FROM dividend_spans WHERE symbol = ?", (symbol,)).fetchone()
        return (date.fromisoformat(row[0]), date.fromisoformat(row[1])) if row else None

    def cover(self, symbol: str, first: date, last: date, replace: bool = False) -> bool:
        """Add an asked window to the span when it touches it (or there is none, or `replace`);
        False when a gap would open, leaving the span as it was."""
        span = self.span(symbol)
        if span is not None and not replace:
            if first > span[1] + timedelta(days=1) or last < span[0] - timedelta(days=1):
                return False
            first, last = min(first, span[0]), max(last, span[1])
        with transaction(self._conn):
            self._conn.execute("INSERT OR REPLACE INTO dividend_spans (symbol, first, last) VALUES (?, ?, ?)",
                               (symbol, first.isoformat(), last.isoformat()))
        return True


@dataclass(frozen=True)
class PaperDividend:
    account: str
    source: str
    """The order source whose shares earned it (a sleeve of the account, or the account itself)."""
    symbol: str
    ex_date: date
    pay_date: date
    """The day it was credited: the source's pay date, or the assumed one when `pay_known` is False."""
    pay_known: bool
    shares: int
    """Held at the close before the ex-date."""
    amount: float
    """Per share, in `currency`."""
    currency: str
    rate: float
    """Units of `currency` per base unit on the pay date."""
    cash: float
    """Credited, base currency."""


_CREDIT = "account, source, symbol, ex_date, pay_date, pay_known, shares, amount, currency, rate, cash"


class PaperDividendRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, c: PaperDividend) -> bool:
        """False when that credit is already there."""
        with transaction(self._conn):
            return self._conn.execute(
                f"INSERT OR IGNORE INTO paper_dividends ({_CREDIT}) VALUES ({', '.join('?' * 11)})",
                (c.account, c.source, c.symbol, c.ex_date.isoformat(), c.pay_date.isoformat(), int(c.pay_known),
                 c.shares, c.amount, c.currency, c.rate, c.cash)).rowcount == 1

    def credits(self, account: str, source: str | None = None) -> list[PaperDividend]:
        """Oldest pay date first; with `source`, a sleeve's alone."""
        rows = self._conn.execute(
            f"SELECT {_CREDIT} FROM paper_dividends WHERE account = :a AND (:s IS NULL OR source = :s) "
            "ORDER BY pay_date, symbol", {"a": account, "s": source})
        return [PaperDividend(a, s, sym, date.fromisoformat(e), date.fromisoformat(p), bool(k), *rest)
                for a, s, sym, e, p, k, *rest in rows]

    def credited(self, account: str) -> set[tuple[str, str, date]]:
        """(source, symbol, ex-date) of every credit on the account."""
        return {(s, sym, date.fromisoformat(e)) for s, sym, e in self._conn.execute(
            "SELECT source, symbol, ex_date FROM paper_dividends WHERE account = ?", (account,))}
