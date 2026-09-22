from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Rates from a history-capable source mean history was filled; open.er-api has today only.
DDL = """
CREATE TABLE fx_history (
    base     TEXT NOT NULL,
    currency TEXT NOT NULL,
    filled   INTEGER NOT NULL DEFAULT 0,
    tried_on TEXT,
    PRIMARY KEY (base, currency)
) WITHOUT ROWID;
INSERT INTO fx_history (base, currency, filled)
    SELECT DISTINCT base, currency, 1 FROM fx_rates WHERE source != 'open_er_api';
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
