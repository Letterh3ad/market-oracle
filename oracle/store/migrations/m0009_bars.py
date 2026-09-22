from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
CREATE TABLE bars (
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL NOT NULL,
    adj_close  REAL,
    volume     INTEGER,
    currency   TEXT NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
