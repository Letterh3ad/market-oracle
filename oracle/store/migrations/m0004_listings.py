from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

DDL = """
CREATE TABLE listings (
    symbol         TEXT PRIMARY KEY,
    name           TEXT    NOT NULL,
    type           TEXT    NOT NULL,
    company_key    TEXT    NOT NULL,
    is_primary     INTEGER NOT NULL,
    delisted       INTEGER NOT NULL,
    country        TEXT,
    exchange       TEXT,
    market         TEXT,
    currency       TEXT,
    isin           TEXT,
    sector         TEXT,
    industry_group TEXT,
    industry       TEXT,
    market_cap     TEXT,
    website        TEXT,
    summary        TEXT
);
CREATE INDEX listings_company ON listings (company_key);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
