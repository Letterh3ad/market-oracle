from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The regime-ai line's daily model tilt, or why there was none, one row per account and market
# day: a restart or a second step that day reuses it, and every later book replays the tilts
# that were applied instead of asking the model about the past.
DDL = """
CREATE TABLE regime_tilts (
    account TEXT NOT NULL,
    day     TEXT NOT NULL,
    tilt    TEXT NOT NULL,
    rules   TEXT NOT NULL,
    reason  TEXT,
    error   TEXT,
    model   TEXT,
    at      TEXT NOT NULL,
    PRIMARY KEY (account, day)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
