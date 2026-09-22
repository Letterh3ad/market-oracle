from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Figures as JSON: which ones sources supply will grow, and each snapshot is read whole.
DDL = """
CREATE TABLE fundamentals (
    symbol     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
