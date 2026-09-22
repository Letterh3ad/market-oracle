from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# One row per analyst per data date: rerunning on unchanged data replaces it, new data adds
# history. Figures as JSON, since each analyst names its own.
DDL = """
CREATE TABLE signals (
    symbol      TEXT NOT NULL,
    analyst     TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    score       REAL NOT NULL,
    confidence  REAL NOT NULL,
    reason      TEXT NOT NULL,
    figures     TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (symbol, analyst, as_of)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
