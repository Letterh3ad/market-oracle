from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The query that first found an article, and the text of evidence Market Oracle wrote itself
# (a run's stored-data summary), so a report can be checked later against what it read.
DDL = """
ALTER TABLE articles ADD COLUMN query TEXT;
ALTER TABLE articles ADD COLUMN body TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
