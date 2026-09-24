from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# What the universe screen says of a shortlisted company ("universe: Semiconductors &
# Semiconductor Equipment, Netherlands, Mega Cap"); NULL when only the news named it.
DDL = """
ALTER TABLE discovery_answer_items ADD COLUMN universe TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
