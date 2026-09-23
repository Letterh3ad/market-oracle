from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Leans keyed by source too, so AI leans (phase 8) sit beside the baseline for comparison.
DDL = """
CREATE TABLE leans (
    symbol      TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    source      TEXT NOT NULL,
    label       TEXT NOT NULL,
    probability REAL NOT NULL,
    reason      TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (symbol, source, as_of)
) WITHOUT ROWID;
CREATE TABLE strategy_decisions (
    id       INTEGER PRIMARY KEY,
    day      TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    action   TEXT NOT NULL,
    cash     REAL,
    reason   TEXT NOT NULL,
    order_id INTEGER REFERENCES paper_orders (id)
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
