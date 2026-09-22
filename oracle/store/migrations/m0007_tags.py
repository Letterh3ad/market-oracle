from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# One row per listing of the tagged company, not a company key: grouping can change on
# a refresh, and the tag lives while any of those listings does. `via` is the symbol
# the user tagged; a tag whose rows from that `via` all left the universe is orphaned.
DDL = """
CREATE TABLE tags (
    symbol     TEXT NOT NULL,
    tag        TEXT NOT NULL,
    via        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, tag)
);
CREATE INDEX tags_tag ON tags (tag);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
