from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The index backfill: member lists cached per index, the lines it added or corrected (re-applied
# after every FinanceDatabase refresh), and the members Yahoo could not add, so a run skips them.
DDL = """
ALTER TABLE listings ADD COLUMN source TEXT;
CREATE TABLE index_members (
    index_code TEXT    NOT NULL,
    position   INTEGER NOT NULL,
    symbol     TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    isin       TEXT,
    PRIMARY KEY (index_code, symbol)
);
CREATE TABLE index_pages (
    index_code TEXT PRIMARY KEY,
    tried_at   TEXT NOT NULL,
    fetched_at TEXT,
    error      TEXT
);
CREATE TABLE backfill_rows (
    symbol     TEXT PRIMARY KEY COLLATE NOCASE,
    kind       TEXT NOT NULL,
    index_code TEXT NOT NULL,
    note       TEXT NOT NULL,
    data       TEXT NOT NULL,
    at         TEXT NOT NULL
);
CREATE TABLE backfill_tries (
    symbol     TEXT PRIMARY KEY COLLATE NOCASE,
    index_code TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    failed     INTEGER NOT NULL,
    tried_at   TEXT NOT NULL
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
