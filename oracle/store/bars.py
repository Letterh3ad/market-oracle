from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date

from oracle.market.bars import Bar
from oracle.store.db import transaction

_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "currency", "source")


class BarRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, bars: Sequence[Bar], fetched_at: str) -> int:
        """Store bars, replacing any for the same day; returns how many days were new."""
        if not bars:
            return 0
        with transaction(self._conn):
            known = {d for (d,) in self._conn.execute(
                "SELECT date FROM bars WHERE symbol = ? AND date >= ?",
                (bars[0].symbol, min(b.date for b in bars).isoformat()),
            )}
            self._conn.executemany(
                f"INSERT OR REPLACE INTO bars ({', '.join(_COLUMNS)}, fetched_at) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)}, ?)",
                [(*(_value(b, c) for c in _COLUMNS), fetched_at) for b in bars],
            )
        return sum(1 for b in {b.date for b in bars} if b.isoformat() not in known)

    def currencies(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT DISTINCT currency FROM bars ORDER BY currency")]

    def last_date(self, symbol: str) -> date | None:
        row = self._conn.execute("SELECT MAX(date) FROM bars WHERE symbol = ?", (symbol,)).fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def span(self, symbols: Sequence[str]) -> tuple[date, date] | None:
        """The first and last stored day across `symbols`, None with no bars."""
        if not symbols:
            return None
        row = self._conn.execute(
            f"SELECT MIN(date), MAX(date) FROM bars WHERE symbol IN ({', '.join('?' for _ in symbols)})",
            tuple(symbols),
        ).fetchone()
        return (date.fromisoformat(row[0]), date.fromisoformat(row[1])) if row[0] else None

    def first_between(self, symbol: str, start: date, end: date) -> date | None:
        """The first stored trading day from `start` to `end`, both included."""
        row = self._conn.execute("SELECT MIN(date) FROM bars WHERE symbol = ? AND date BETWEEN ? AND ?",
                                 (symbol, start.isoformat(), end.isoformat())).fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def close_on(self, symbol: str, day: date | None = None) -> float | None:
        """The last close on or before `day` (the latest close without one)."""
        row = self._conn.execute(
            "SELECT close FROM bars WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (symbol, (day or date.max).isoformat()),
        ).fetchone()
        return row[0] if row else None

    def history(self, symbol: str, limit: int | None = None) -> list[Bar]:
        """Oldest first; with `limit`, only the most recent that many."""
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM bars WHERE symbol = ? ORDER BY date DESC"
            + (" LIMIT ?" if limit is not None else ""),
            (symbol, limit) if limit is not None else (symbol,),
        ).fetchall()
        return [Bar(**{**dict(zip(_COLUMNS, r)), "date": date.fromisoformat(r[1])}) for r in reversed(rows)]


    def since(self, symbol: str, start: date, end: date | None = None) -> list[Bar]:
        """Oldest first, from `start` on (to `end`, included, when given)."""
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM bars WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY date",
            (symbol, start.isoformat(), (end or date.max).isoformat()),
        ).fetchall()
        return [Bar(**{**dict(zip(_COLUMNS, r)), "date": date.fromisoformat(r[1])}) for r in rows]


def _value(bar: Bar, column: str):
    value = getattr(bar, column)
    return value.isoformat() if isinstance(value, date) else value
