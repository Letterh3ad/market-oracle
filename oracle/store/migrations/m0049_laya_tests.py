from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Kept results of the Laya predictive test (ticket 06): `run` is main or sensitivity, `verdict`
# PASS, FAIL or INCONCLUSIVE, `result` the whole table as JSON.
DDL = """
CREATE TABLE laya_tests (
    id      INTEGER PRIMARY KEY,
    run     TEXT NOT NULL,
    model   TEXT NOT NULL,
    verdict TEXT NOT NULL,
    result  TEXT NOT NULL,
    run_at  TEXT NOT NULL
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
