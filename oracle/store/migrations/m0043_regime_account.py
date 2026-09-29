from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The regime account logs each decision with its explanation (mood, each method's part of the
# score), and each step's regime reading, so its Ledgers card needs no rebuild of the book.
# Keyed by account so a second regime line shares the table.
DDL = """
ALTER TABLE strategy_decisions ADD COLUMN explanation TEXT;
CREATE TABLE regime_states (
    account TEXT NOT NULL,
    day     TEXT NOT NULL,
    state   TEXT NOT NULL,
    at      TEXT NOT NULL,
    PRIMARY KEY (account, day)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
