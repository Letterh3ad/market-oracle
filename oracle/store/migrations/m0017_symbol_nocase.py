from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Symbols are looked up in any case; the primary key's index is case-sensitive, so without
# this every lookup scanned the whole universe.
DDL = """
CREATE INDEX listings_symbol_nocase ON listings (symbol COLLATE NOCASE);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
