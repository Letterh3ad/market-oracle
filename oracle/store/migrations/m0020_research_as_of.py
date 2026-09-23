from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# NULL: researched with everything known when it ran.
DDL = """
ALTER TABLE research_runs ADD COLUMN as_of TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
