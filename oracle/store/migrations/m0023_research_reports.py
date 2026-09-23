from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The report as JSON: read back whole, never queried inside.
DDL = """
CREATE TABLE research_reports (
    run_id INTEGER PRIMARY KEY REFERENCES research_runs (id),
    symbol TEXT NOT NULL,
    day    TEXT NOT NULL,
    body   TEXT NOT NULL
);
CREATE INDEX research_reports_symbol ON research_reports (symbol, day);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
