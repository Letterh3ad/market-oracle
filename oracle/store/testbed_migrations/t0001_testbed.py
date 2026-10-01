from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# tb_membership keeps the snapshot's rows as published: each row is the whole member list from
# that day until the next row's day. Tickers are stored in Yahoo's spelling (BRK-B).
DDL = """
CREATE TABLE tb_snapshot (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    source     TEXT    NOT NULL,
    url        TEXT    NOT NULL,
    revision   TEXT,
    fetched_at TEXT    NOT NULL,
    first_day  TEXT    NOT NULL,
    last_day   TEXT    NOT NULL,
    changes    INTEGER NOT NULL
);

CREATE TABLE tb_membership (
    day     TEXT PRIMARY KEY,
    tickers TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE tb_members (
    ticker          TEXT PRIMARY KEY,
    sector          TEXT,
    sector_source   TEXT,
    sector_tried_at TEXT,
    bars_tried_at   TEXT,
    bars_note       TEXT
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
