from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
CREATE TABLE research_runs (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    backend     TEXT NOT NULL,
    model       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    findings    TEXT,
    error       TEXT
);
CREATE INDEX research_runs_symbol ON research_runs (symbol, started_at);
CREATE TABLE research_steps (
    run_id      INTEGER NOT NULL REFERENCES research_runs (id),
    n           INTEGER NOT NULL,
    thought     TEXT NOT NULL,
    action      TEXT NOT NULL,
    input       TEXT NOT NULL,
    observation TEXT,
    PRIMARY KEY (run_id, n)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
