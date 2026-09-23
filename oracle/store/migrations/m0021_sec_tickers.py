from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# SEC's ticker map, refreshed weekly: ~10k rows, fetched in one request.
DDL = """
CREATE TABLE sec_tickers (
    ticker     TEXT PRIMARY KEY,
    cik        INTEGER NOT NULL,
    name       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
