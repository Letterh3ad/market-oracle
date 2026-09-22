from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Items hold symbols, not listing rows: listings are replaced on every refresh, and a
# watched symbol that leaves the universe is flagged rather than dropped.
DDL = """
CREATE TABLE watchlists (
    name       TEXT PRIMARY KEY COLLATE NOCASE,
    created_at TEXT NOT NULL
);
CREATE TABLE watchlist_items (
    watchlist TEXT NOT NULL REFERENCES watchlists (name) ON DELETE CASCADE,
    symbol    TEXT NOT NULL,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (watchlist, symbol)
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
