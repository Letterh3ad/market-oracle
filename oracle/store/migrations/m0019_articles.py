from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# One row per URL whichever search found it; runs link to what they were shown.
DDL = """
CREATE TABLE articles (
    id           INTEGER PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    outlet       TEXT,
    published_at TEXT,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);
CREATE TABLE research_evidence (
    run_id     INTEGER NOT NULL REFERENCES research_runs (id),
    article_id INTEGER NOT NULL REFERENCES articles (id),
    cited      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, article_id)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
