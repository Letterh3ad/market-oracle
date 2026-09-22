from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from oracle.store.db import transaction


@dataclass(frozen=True)
class Order:
    id: int
    account: str
    symbol: str
    side: str
    cash: float | None
    """Base-currency amount, for a buy."""
    shares: int | None
    """For a sell; None sells the whole position."""
    placed_on: date
    status: str
    note: str | None
    source: str


@dataclass(frozen=True)
class FillRow:
    order_id: int
    account: str
    symbol: str
    side: str
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

    def add_order(self, account: str, symbol: str, side: str, cash: float | None, shares: int | None,
                  placed_at: str, placed_on: date, source: str) -> Order:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO paper_orders (account, symbol, side, cash, shares, placed_at, placed_on, status, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (account, symbol, side, cash, shares, placed_at, placed_on.isoformat(), source),
            )
        return Order(cur.lastrowid, account, symbol, side, cash, shares, placed_on, "pending", None, source)

    def pending(self, account: str | None = None) -> list[Order]:
        sql = f"SELECT {_ORDER} FROM paper_orders WHERE status = 'pending'"
        rows = self._conn.execute(sql + (" AND account = ?" if account else "") + " ORDER BY id",
                                  (account,) if account else ())
        return [_order(r) for r in rows]

    def set_status(self, order_id: int, status: str, note: str | None) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE paper_orders SET status = ?, note = ? WHERE id = ?", (status, note, order_id))

    def record_fill(self, row: FillRow) -> None:
        with transaction(self._conn):
            self._conn.execute(
                f"INSERT INTO paper_fills ({_FILL}) VALUES ({', '.join('?' * 14)})",
                (row.order_id, row.account, row.symbol, row.side, row.date.isoformat(), row.shares, row.price,
                 row.currency, row.rate, row.cash, row.fee, row.stamp, row.fx, row.slippage),
            )
            self._conn.execute("UPDATE paper_orders SET status = 'filled', note = NULL WHERE id = ?", (row.order_id,))

    def fills(self, account: str) -> list[FillRow]:
        """Oldest first."""
        rows = self._conn.execute(f"SELECT {_FILL} FROM paper_fills WHERE account = ? ORDER BY date, order_id",
                                  (account,))
        return [FillRow(*r[:4], date.fromisoformat(r[4]), *r[5:]) for r in rows]


def _order(r) -> Order:
    return Order(r[0], r[1], r[2], r[3], r[4], r[5], date.fromisoformat(r[6]), r[7], r[8], r[9])
