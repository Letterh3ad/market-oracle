from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# External content: the index holds tokens only, and is rebuilt after every refresh.
DDL = """
CREATE VIRTUAL TABLE listings_fts USING fts5(
    symbol, name,
    content = 'listings', content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);
INSERT INTO listings_fts (listings_fts) VALUES ('rebuild');
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
