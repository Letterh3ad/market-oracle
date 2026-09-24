from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Replay study (replay-study 02). The picked listings are JSON: fixed at creation, only read
# whole. A cell's `position` is its place in the queue; an automatic retry moves it to the end.
DDL = """
CREATE TABLE replay_studies (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    state         TEXT NOT NULL,
    first_day     TEXT NOT NULL,
    last_day      TEXT NOT NULL,
    listings      TEXT NOT NULL,
    paused_reason TEXT
);
CREATE TABLE replay_cells (
    id          INTEGER PRIMARY KEY,
    study_id    INTEGER NOT NULL REFERENCES replay_studies (id),
    symbol      TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    state       TEXT NOT NULL,
    position    INTEGER NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    job_id      INTEGER,
    run_id      INTEGER,
    error       TEXT,
    started_at  TEXT,
    finished_at TEXT,
    UNIQUE (study_id, symbol, as_of)
);
CREATE INDEX replay_cells_queue ON replay_cells (study_id, state, position);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
