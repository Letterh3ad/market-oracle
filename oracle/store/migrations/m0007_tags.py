from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Keyed by the symbol the user tagged, not a company key: grouping can change on a
# refresh, and a symbol that leaves the universe is reported as orphaned, not dropped.
DDL = """
CREATE TABLE tags (
    symbol     TEXT NOT NULL,
    tag        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, tag)
);
CREATE INDEX tags_tag ON tags (tag);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
