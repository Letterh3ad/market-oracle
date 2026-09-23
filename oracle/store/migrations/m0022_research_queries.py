from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
CREATE TABLE research_queries (
    run_id INTEGER NOT NULL REFERENCES research_runs (id),
    n      INTEGER NOT NULL,
    query  TEXT NOT NULL,
    PRIMARY KEY (run_id, n)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
