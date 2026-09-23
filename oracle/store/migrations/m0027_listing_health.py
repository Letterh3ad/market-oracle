from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Apart from `listings`, which a universe refresh replaces whole. `no_data_days` is a JSON
# list of the distinct days every price source answered "no data" since bars last came.
DDL = """
CREATE TABLE listing_health (
    symbol       TEXT PRIMARY KEY COLLATE NOCASE,
    no_data_days TEXT NOT NULL,
    dead_since   TEXT
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
