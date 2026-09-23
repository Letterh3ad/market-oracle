from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# `stages` is a JSON list of per-stage outcomes, in the order they ran; empty when the
# tick never started (`note` says why).
DDL = """
CREATE TABLE tick_runs (
    id          INTEGER PRIMARY KEY,
    trigger     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stages      TEXT NOT NULL,
    note        TEXT
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
