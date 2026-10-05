from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# research-lab 07. One full text per source, the newest kept (an inbox copy replaces an open-access
# preprint); `via` says where it came from. lit_wanted is "papers I'd like": sources with no legal copy
# found, for the user to fetch through their library and drop in the inbox.
DDL = """
CREATE TABLE lit_full_texts (
    source_id INTEGER PRIMARY KEY REFERENCES lit_sources(id) ON DELETE CASCADE,
    text      TEXT NOT NULL,
    via       TEXT NOT NULL CHECK (via IN ('unpaywall', 'semantic-scholar', 'inbox')),
    url       TEXT,
    licence   TEXT NOT NULL DEFAULT '',
    version   TEXT NOT NULL DEFAULT '',
    file      TEXT,
    pages     INTEGER NOT NULL,
    at        TEXT NOT NULL,
    by        TEXT NOT NULL
);
CREATE TABLE lit_wanted (
    source_id INTEGER PRIMARY KEY REFERENCES lit_sources(id) ON DELETE CASCADE,
    reason    TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    at        TEXT NOT NULL,
    by        TEXT NOT NULL
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
