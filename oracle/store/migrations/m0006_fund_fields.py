from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
ALTER TABLE listings ADD COLUMN category_group TEXT;
ALTER TABLE listings ADD COLUMN category TEXT;
ALTER TABLE listings ADD COLUMN family TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
