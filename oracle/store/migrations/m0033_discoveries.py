from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Why each company was discovered, kept after it is moved or removed. Removals are their own
# table, by company and symbol both, so a company the user threw out never comes back, even
# when a universe refresh gives it a new key or it was added to the list by hand.
DDL = """
CREATE TABLE discoveries (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    company_key TEXT NOT NULL,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    seed        TEXT NOT NULL,
    relation    TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    moved_at    TEXT
);
CREATE INDEX discoveries_symbol ON discoveries (symbol);
CREATE TABLE discovery_evidence (
    discovery_id INTEGER NOT NULL REFERENCES discoveries (id) ON DELETE CASCADE,
    article_id   INTEGER NOT NULL REFERENCES articles (id),
    PRIMARY KEY (discovery_id, article_id)
);
CREATE TABLE discovery_removed (
    symbol      TEXT NOT NULL PRIMARY KEY COLLATE NOCASE,
    company_key TEXT,
    name        TEXT,
    removed_at  TEXT NOT NULL
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
