from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The models that answered after the configured one refused, as a JSON list: kept apart
# from `model` so the configured model stays a plain id.
DDL = """
ALTER TABLE research_runs ADD COLUMN fell_back_to TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
