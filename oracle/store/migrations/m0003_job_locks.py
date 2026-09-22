from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
CREATE TABLE job_locks (
    job          TEXT PRIMARY KEY,
    token        TEXT    NOT NULL,
    pid          INTEGER NOT NULL,
    host         TEXT    NOT NULL,
    acquired_at  REAL    NOT NULL,
    heartbeat_at REAL    NOT NULL,
    lease_s      REAL    NOT NULL
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
