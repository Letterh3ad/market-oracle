from __future__ import annotations

import json
import sqlite3
from datetime import date

from oracle.analysts.signals import AnalystSignal
from oracle.store.db import transaction

_COLUMNS = ("analyst", "symbol", "as_of", "score", "confidence", "reason", "figures")


class SignalRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, signals: list[AnalystSignal], computed_at: str) -> None:
        with transaction(self._conn):
            self._conn.executemany(
                f"INSERT OR REPLACE INTO signals ({', '.join(_COLUMNS)}, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(s.analyst, s.symbol, s.as_of.isoformat(), s.score, s.confidence, s.reason,
                  json.dumps(s.figures), computed_at) for s in signals],
            )

    def history(self, symbol: str, analyst: str) -> list[AnalystSignal]:
        """Oldest first."""
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM signals WHERE symbol = ? AND analyst = ? ORDER BY as_of",
            (symbol, analyst),
        )
        return [_signal(r) for r in rows]

    def latest(self, symbol: str) -> list[AnalystSignal]:
        """Each analyst's most recent signal for the listing."""
        rows = self._conn.execute(
            f"""SELECT {', '.join(_COLUMNS)} FROM signals s WHERE symbol = ?
                AND as_of = (SELECT MAX(as_of) FROM signals WHERE symbol = s.symbol AND analyst = s.analyst)""",
            (symbol,),
        )
        return [_signal(r) for r in rows]


def _signal(r) -> AnalystSignal:
    return AnalystSignal(r[0], r[1], date.fromisoformat(r[2]), r[3], r[4], r[5], tuple(tuple(f) for f in json.loads(r[6])))
