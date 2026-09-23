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


def _value(bar: Bar, column: str):
    value = getattr(bar, column)
    return value.isoformat() if isinstance(value, date) else value
