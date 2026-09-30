from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from oracle.broker.orders import Action, Side, Status
from oracle.store.db import transaction


@dataclass(frozen=True)
class Order:
    id: int
    account: str
    symbol: str
    side: Side
    cash: float | None
    """Base-currency amount, for a buy."""
    shares: int | None
    """For a sell; None sells the whole position."""
    placed_on: date
    status: Status
    note: str | None
    source: str


@dataclass(frozen=True)
class FillRow:
    order_id: int
    account: str
    symbol: str
    side: Side
    date: date
    shares: int
    price: float
    currency: str
    rate: float
    cash: float
    fee: float
    stamp: float
    fx: float
    slippage: float


_ORDER = "id, account, symbol, side, cash, shares, placed_on, status, note, source"
_FILL = "order_id, account, symbol, side, date, shares, price, currency, rate, cash, fee, stamp, fx, slippage"


class PaperRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_order(self, account: str, symbol: str, side: Side, cash: float | None, shares: int | None,
                  placed_at: str, placed_on: date, source: str) -> Order:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO paper_orders (account, symbol, side, cash, shares, placed_at, placed_on, status, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account, symbol, side, cash, shares, placed_at, placed_on.isoformat(), Status.PENDING, source),
            )
        return Order(cur.lastrowid, account, symbol, side, cash, shares, placed_on, Status.PENDING, None, source)

    def pending(self, account: str | None = None) -> list[Order]:
        sql = f"SELECT {_ORDER} FROM paper_orders WHERE status = ?"
        rows = self._conn.execute(sql + (" AND account = ?" if account else "") + " ORDER BY id",
                                  (Status.PENDING, account) if account else (Status.PENDING,))
        return [_order(r) for r in rows]

    def set_status(self, order_id: int, status: Status, note: str | None) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE paper_orders SET status = ?, note = ? WHERE id = ?", (status, note, order_id))

    def record_fill(self, row: FillRow) -> None:
        with transaction(self._conn):
            self._conn.execute(
                f"INSERT INTO paper_fills ({_FILL}) VALUES ({', '.join('?' * 14)})",
                (row.order_id, row.account, row.symbol, row.side, row.date.isoformat(), row.shares, row.price,
                 row.currency, row.rate, row.cash, row.fee, row.stamp, row.fx, row.slippage),
            )
            self._conn.execute("UPDATE paper_orders SET status = ?, note = NULL WHERE id = ?",
                               (Status.FILLED, row.order_id))

    def holders(self, symbol: str) -> tuple[str, ...]:
        """The accounts holding shares of `symbol`."""
        return tuple(r[0] for r in self._conn.execute(
            "SELECT account FROM paper_fills WHERE symbol = ? GROUP BY account "
            "HAVING SUM(CASE side WHEN 'buy' THEN shares ELSE -shares END) > 0 ORDER BY account", (symbol,)))

    def held_symbols(self, account: str | None = None) -> list[str]:
        """The symbols with shares held, on one account or on any, sorted."""
        return sorted({r[0] for r in self._conn.execute(
            "SELECT symbol FROM paper_fills WHERE (:a IS NULL OR account = :a) GROUP BY account, symbol "
            "HAVING SUM(CASE side WHEN 'buy' THEN shares ELSE -shares END) > 0", {"a": account})})

    def fills(self, account: str, source: str | None = None) -> list[FillRow]:
        """Oldest first; with `source`, only the fills of orders placed by it (a sleeve of the account)."""
        cols = ", ".join(f"f.{c}" for c in _FILL.split(", "))
        rows = self._conn.execute(
            f"SELECT {cols} FROM paper_fills f LEFT JOIN paper_orders o ON o.id = f.order_id WHERE f.account = :a "
            "AND (:s IS NULL OR o.source = :s) ORDER BY f.date, f.order_id", {"a": account, "s": source})
        return [FillRow(*r[:3], Side(r[3]), date.fromisoformat(r[4]), *r[5:]) for r in rows]

    def orders(self, account: str, source: str) -> list[Order]:
        """Every order `source` placed on the account, whatever became of it, oldest first."""
        rows = self._conn.execute(f"SELECT {_ORDER} FROM paper_orders WHERE account = ? AND source = ? ORDER BY id",
                                  (account, source))
        return [_order(r) for r in rows]


def _order(r) -> Order:
    return Order(r[0], r[1], r[2], Side(r[3]), r[4], r[5], date.fromisoformat(r[6]), Status(r[7]), r[8], r[9])


class EquityRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, account: str, day: date, cash: float, holdings: float) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO paper_equity (account, date, cash, holdings) VALUES (?, ?, ?, ?)",
                (account, day.isoformat(), cash, holdings),
            )

    def history(self, account: str) -> list[tuple[date, float]]:
        """(date, equity), oldest first."""
        rows = self._conn.execute(
            "SELECT date, cash + holdings FROM paper_equity WHERE account = ? ORDER BY date", (account,)
        )
        return [(date.fromisoformat(d), v) for d, v in rows]


@dataclass(frozen=True)
class LoggedDecision:
    account: str
    day: date
    symbol: str
    action: Action
    cash: float | None
    reason: str
    order_id: int | None
    report_id: int | None = None
    """The research run whose verdict the AI account acted on."""
    needs_research: bool = False
    explanation: dict | None = None
    """What a regime account's lean was made of: its mood and each method's part of the score."""


_DECISION = "account, day, symbol, action, cash, reason, order_id, report_id, needs_research, explanation"


class DecisionRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def log(self, rows: list[LoggedDecision]) -> None:
        with transaction(self._conn):
            self._conn.executemany(
                f"INSERT INTO strategy_decisions ({_DECISION}) VALUES ({', '.join('?' * 10)})",
                [(r.account, r.day.isoformat(), r.symbol, r.action, r.cash, r.reason, r.order_id, r.report_id,
                  r.needs_research, None if r.explanation is None else json.dumps(r.explanation)) for r in rows],
            )

    def recent(self, account: str, limit: int = 50) -> list[LoggedDecision]:
        """Newest first."""
        rows = self._conn.execute(
            f"SELECT {_DECISION} FROM strategy_decisions WHERE account = ? ORDER BY id DESC LIMIT ?", (account, limit),
        )
        return [LoggedDecision(r[0], date.fromisoformat(r[1]), r[2], Action(r[3]), *r[4:8], bool(r[8]),
                               None if r[9] is None else json.loads(r[9])) for r in rows]

    def count_on_last_day(self, account: str, prefix: str) -> int:
        """Rows whose reason starts with `prefix`, logged on the account's most recent decision
        day; independent of any window `recent` is asked for."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM strategy_decisions WHERE account = ? AND reason LIKE ? AND day = "
            "(SELECT MAX(day) FROM strategy_decisions WHERE account = ?)",
            (account, f"{prefix}%", account),
        ).fetchone()
        return row[0]
