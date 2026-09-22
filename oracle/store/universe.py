from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, fields

from oracle.store.db import transaction
from oracle.universe.listings import Listing

_COLUMNS = [f.name for f in fields(Listing)]


@dataclass(frozen=True)
class UniverseCounts:
    listings: int
    companies: int
    delisted: int


class UniverseRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_all(self, listings: Iterable[Listing]) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with transaction(self._conn):
            self._conn.execute("DELETE FROM listings")
            self._conn.executemany(
                f"INSERT INTO listings ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                ([getattr(x, c) for c in _COLUMNS] for x in listings),
            )

    def counts(self) -> UniverseCounts:
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT company_key), COALESCE(SUM(delisted), 0) FROM listings"
        ).fetchone()
        return UniverseCounts(*row)
