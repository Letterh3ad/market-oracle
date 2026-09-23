from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The server process that owns a queued or running job, so a starting server sweeps only
# what a dead one left behind.
DDL = "ALTER TABLE jobs ADD COLUMN pid INTEGER;"


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
