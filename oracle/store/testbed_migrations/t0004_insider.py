from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# insider_trades: open-market purchases (P) and sales (S) of original Form 4s, from SEC's quarterly
# data sets. sec_filings: original Form 4s and 8-Ks from each issuer's submissions, accepted in New
# York time (YYYY-MM-DDTHH:MM), and the 8-K items.
DDL = """
CREATE TABLE insider_trades (
    accession  TEXT    NOT NULL,
    row        INTEGER NOT NULL,
    issuer_cik INTEGER NOT NULL,
    owner_cik  INTEGER NOT NULL,
    owner      TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    code       TEXT    NOT NULL,
    traded     TEXT    NOT NULL,
    shares     REAL    NOT NULL,
    price      REAL    NOT NULL,
    filed      TEXT    NOT NULL,
    quarter    TEXT    NOT NULL,
    PRIMARY KEY (accession, row)
) WITHOUT ROWID;
CREATE INDEX insider_trades_issuer ON insider_trades (issuer_cik);
CREATE INDEX insider_trades_quarter ON insider_trades (quarter);

CREATE TABLE insider_quarters (
    quarter    TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    trades     INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE sec_filings (
    accession TEXT    PRIMARY KEY,
    cik       INTEGER NOT NULL,
    form      TEXT    NOT NULL,
    items     TEXT    NOT NULL,
    filed     TEXT    NOT NULL,
    accepted  TEXT
) WITHOUT ROWID;
CREATE INDEX sec_filings_cik ON sec_filings (cik);

CREATE TABLE sec_filings_tried (
    cik      INTEGER PRIMARY KEY,
    tried_at TEXT    NOT NULL,
    filings  INTEGER NOT NULL
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
