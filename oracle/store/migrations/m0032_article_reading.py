from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# What reading an article gave: its text or why it could not be read (a passing failure is
# tried again by the next run), and each model's notes on it, so no run reads or notes twice.
# `attempts` counts unusable notes, so a model that cannot note an article stops trying.
# `site` is the outlet's own domain, which Google News names beside its redirect links.
DDL = """
ALTER TABLE articles ADD COLUMN site TEXT;
CREATE TABLE article_texts (
    article_id INTEGER PRIMARY KEY REFERENCES articles (id),
    read_at    TEXT NOT NULL,
    url        TEXT,
    text       TEXT,
    failed     TEXT,
    passing    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE article_notes (
    article_id INTEGER NOT NULL REFERENCES articles (id),
    model      TEXT NOT NULL,
    noted_at   TEXT NOT NULL,
    about      INTEGER,
    facts      TEXT,
    failed     TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (article_id, model)
) WITHOUT ROWID;
ALTER TABLE research_runs ADD COLUMN stage TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
