from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The replay pacer's memory as JSON (outage back-off, replays it stopped), so a restart
# resumes a back-off and never reads a replay it stopped as the user's cancel.
DDL = """
ALTER TABLE replay_studies ADD COLUMN pacer TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
