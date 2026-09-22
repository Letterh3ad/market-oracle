from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Times are epoch seconds from the wall clock: a monotonic clock is per process, and
# this state is shared between processes.
DDL = """
CREATE TABLE source_state (
    source       TEXT PRIMARY KEY,
    next_allowed REAL    NOT NULL DEFAULT 0,
    run_429      INTEGER NOT NULL DEFAULT 0,
    run_5xx      INTEGER NOT NULL DEFAULT 0,
    run_403      INTEGER NOT NULL DEFAULT 0,
    tripped_at   REAL,
    reason       TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE source_requests (
    source TEXT NOT NULL,
    at     REAL NOT NULL
);
CREATE INDEX idx_source_requests ON source_requests(source, at);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
