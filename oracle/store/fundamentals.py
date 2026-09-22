from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date

from oracle.market.fundamentals import Fundamentals
from oracle.store.db import transaction

_KEYS = ("symbol", "as_of", "source")


class FundamentalsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, snapshot: Fundamentals, fetched_at: str) -> None:
        data = {k: v for k, v in asdict(snapshot).items() if k not in _KEYS}
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO fundamentals (symbol, as_of, source, fetched_at, data) VALUES (?, ?, ?, ?, ?)",
                (snapshot.symbol, snapshot.as_of.isoformat(), snapshot.source, fetched_at, json.dumps(data)),
            )

    def latest(self, symbol: str) -> Fundamentals | None:
        row = self._conn.execute(
            "SELECT symbol, as_of, source, data FROM fundamentals WHERE symbol = ? ORDER BY as_of DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return Fundamentals(symbol=row[0], as_of=date.fromisoformat(row[1]), source=row[2], **json.loads(row[3]))
