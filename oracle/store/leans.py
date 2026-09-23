from __future__ import annotations

import sqlite3
from datetime import date

from oracle.broker.rules import Lean
from oracle.store.db import transaction


class LeanRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, leans: list[Lean], reason: str, computed_at: str) -> None:
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR REPLACE INTO leans (symbol, as_of, source, label, probability, reason, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(x.symbol, x.as_of.isoformat(), x.source, x.label, x.probability, reason, computed_at) for x in leans],
            )

    def latest(self, symbol: str) -> Lean | None:
        row = self._conn.execute(
            "SELECT symbol, as_of, label, probability, source FROM leans WHERE symbol = ? "
            "ORDER BY as_of DESC, computed_at DESC LIMIT 1", (symbol,),
        ).fetchone()
        return Lean(row[0], date.fromisoformat(row[1]), row[2], row[3], row[4]) if row else None
