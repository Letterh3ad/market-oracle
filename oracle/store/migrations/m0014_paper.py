from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Cash and positions are never stored: they are replayed from fills, so they cannot drift.
DDL = """
CREATE TABLE paper_orders (
    id        INTEGER PRIMARY KEY,
    account   TEXT    NOT NULL,
    symbol    TEXT    NOT NULL,
    side      TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
    cash      REAL,
    shares    INTEGER,
    placed_at TEXT    NOT NULL,
    placed_on TEXT    NOT NULL,
    status    TEXT    NOT NULL CHECK (status IN ('pending', 'filled', 'rejected')),
    note      TEXT,
    source    TEXT    NOT NULL
);
CREATE INDEX paper_orders_pending ON paper_orders (status, account);
CREATE TABLE paper_fills (
    order_id  INTEGER PRIMARY KEY REFERENCES paper_orders (id),
    account   TEXT    NOT NULL,
    symbol    TEXT    NOT NULL,
    side      TEXT    NOT NULL,
    date      TEXT    NOT NULL,
    shares    INTEGER NOT NULL,
    price     REAL    NOT NULL,
    currency  TEXT    NOT NULL,
    rate      REAL    NOT NULL,
    cash      REAL    NOT NULL,
    fee       REAL    NOT NULL,
    stamp     REAL    NOT NULL,
    fx        REAL    NOT NULL,
    slippage  REAL    NOT NULL
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
