from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The real-money plan's cool-off (boring-plan 08): a change to a set [plan] value waits here until
# `due_at`. `changes` is JSON {key: [from, to]}; at most one row is pending at a time.
DDL = """
CREATE TABLE plan_changes (
    id         INTEGER PRIMARY KEY,
    changes    TEXT NOT NULL,
    made_at    TEXT NOT NULL,
    due_at     TEXT NOT NULL,
    state      TEXT NOT NULL CHECK (state IN ('pending', 'applied', 'cancelled', 'refused')),
    settled_at TEXT,
    note       TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX plan_changes_one_pending ON plan_changes (state) WHERE state = 'pending';
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
