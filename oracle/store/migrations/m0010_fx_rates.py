from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Keyed by base too, so changing base_currency never mixes rates.
DDL = """
CREATE TABLE fx_rates (
    base           TEXT NOT NULL,
    currency       TEXT NOT NULL,
    date           TEXT NOT NULL,
    units_per_base REAL NOT NULL,
    source         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (base, currency, date)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
