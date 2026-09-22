from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date

from oracle.market.fx import FxRate
from oracle.store.db import transaction


class FxRepo:
    def __init__(self, conn: sqlite3.Connection, base: str) -> None:
        self._conn = conn
        self._base = base

    def upsert(self, rates: Sequence[FxRate], fetched_at: str) -> int:
        """Store rates, replacing any for the same day; returns how many days were new."""
        if not rates:
            return 0
        currency = rates[0].currency
        with transaction(self._conn):
            known = {d for (d,) in self._conn.execute(
                "SELECT date FROM fx_rates WHERE base = ? AND currency = ?", (self._base, currency)
            )}
            self._conn.executemany(
                "INSERT OR REPLACE INTO fx_rates (base, currency, date, units_per_base, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(self._base, r.currency, r.date.isoformat(), r.units_per_base, r.source, fetched_at) for r in rates],
            )
        return len({r.date.isoformat() for r in rates} - known)

    def last_date(self, currency: str) -> date | None:
        row = self._conn.execute(
            "SELECT MAX(date) FROM fx_rates WHERE base = ? AND currency = ?", (self._base, currency)
        ).fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def first_date(self, currency: str) -> date | None:
        row = self._conn.execute(
            "SELECT MIN(date) FROM fx_rates WHERE base = ? AND currency = ?", (self._base, currency)
        ).fetchone()
        return date.fromisoformat(row[0]) if row[0] else None

    def history(self, currency: str, limit: int | None = None) -> list[FxRate]:
        """Oldest first; with `limit`, only the most recent that many."""
        rows = self._conn.execute(
            "SELECT currency, date, units_per_base, source FROM fx_rates WHERE base = ? AND currency = ? "
            "ORDER BY date DESC" + (" LIMIT ?" if limit is not None else ""),
            (self._base, currency, limit) if limit is not None else (self._base, currency),
        ).fetchall()
        return [FxRate(r[0], date.fromisoformat(r[1]), r[2], r[3]) for r in reversed(rows)]
