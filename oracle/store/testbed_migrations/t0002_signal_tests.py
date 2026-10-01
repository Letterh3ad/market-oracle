from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
CREATE TABLE signal_tests (
    id      INTEGER PRIMARY KEY,
    signal  TEXT NOT NULL,
    verdict TEXT NOT NULL,
    result  TEXT NOT NULL,
    run_at  TEXT NOT NULL
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
