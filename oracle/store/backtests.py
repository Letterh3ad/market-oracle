from __future__ import annotations

import sqlite3

from oracle.store.db import transaction

# Kept in `meta` rather than a table of their own: a migration's number is its place in the
# list, so one added here beside discovery's would clash whichever branch merged second.
PREFIX = "backtest_run:"
_ALL = f"{PREFIX}*"


def _key(run_id: int) -> str:
    return f"{PREFIX}{run_id:09d}"  # zero-padded, so the keys sort by id


class BacktestRepo:
    """Backtest results as JSON, newest first, only the last few."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, record: str, keep: int) -> int:
        """Store `record` under the next id and drop all but the newest `keep`."""
        with transaction(self._conn):
            last = self._conn.execute("SELECT MAX(key) FROM meta WHERE key GLOB ?", (_ALL,)).fetchone()[0]
            run_id = int(last[len(PREFIX):]) + 1 if last else 1
            self._conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (_key(run_id), record))
            self._conn.execute(
                "DELETE FROM meta WHERE key GLOB ? AND key NOT IN "
                "(SELECT key FROM meta WHERE key GLOB ? ORDER BY key DESC LIMIT ?)", (_ALL, _ALL, keep))
        return run_id

    def recent(self) -> list[tuple[int, str]]:
        rows = self._conn.execute("SELECT key, value FROM meta WHERE key GLOB ? ORDER BY key DESC", (_ALL,))
        return [(int(r["key"][len(PREFIX):]), r["value"]) for r in rows]

    def get(self, run_id: int) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (_key(run_id),)).fetchone()
        return None if row is None else row["value"]
