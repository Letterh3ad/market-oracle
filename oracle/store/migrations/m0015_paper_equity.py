from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
CREATE TABLE paper_equity (
    account  TEXT NOT NULL,
    date     TEXT NOT NULL,
    cash     REAL NOT NULL,
    holdings REAL NOT NULL,
    PRIMARY KEY (account, date)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
