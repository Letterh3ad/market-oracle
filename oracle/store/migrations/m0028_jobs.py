from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Jobs the GUI server ran, for the Activity tab's history. Queued and running rows exist
# only while a server runs; a new one sweeps any left behind.
DDL = """
CREATE TABLE jobs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    label        TEXT NOT NULL,
    subject      TEXT,
    link         TEXT,
    state        TEXT NOT NULL,
    error        TEXT,
    submitted_at TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX jobs_finished ON jobs (finished_at);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
