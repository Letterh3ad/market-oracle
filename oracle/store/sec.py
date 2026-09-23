from __future__ import annotations

import sqlite3

from oracle.store.db import transaction


class SecTickerRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace(self, tickers: dict[str, tuple[int, str]], fetched_at: str) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM sec_tickers")
            self._conn.executemany(
                "INSERT INTO sec_tickers (ticker, cik, name, fetched_at) VALUES (?, ?, ?, ?)",
                [(t, cik, name, fetched_at) for t, (cik, name) in tickers.items()],
            )

    def fetched_at(self) -> str | None:
        return self._conn.execute("SELECT MIN(fetched_at) FROM sec_tickers").fetchone()[0]

    def lookup(self, ticker: str) -> tuple[int, str] | None:
        row = self._conn.execute("SELECT cik, name FROM sec_tickers WHERE ticker = ?", (ticker.upper(),)).fetchone()
        return (row[0], row[1]) if row else None
