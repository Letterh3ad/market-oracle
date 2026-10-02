from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Cash dividends per listing (boring-plan ticket 01), the span of days they were asked for (a
# listing with no row was never asked, so its total return is unknown, not price only), and
# the paper ledgers' dividend credits, one per account, order source, listing and ex-date.
DDL = """
CREATE TABLE dividends (
    symbol      TEXT NOT NULL,
    ex_date     TEXT NOT NULL,
    amount      REAL NOT NULL,
    currency    TEXT NOT NULL,
    pay_date    TEXT,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, ex_date)
) WITHOUT ROWID;

CREATE TABLE dividend_spans (
    symbol     TEXT NOT NULL PRIMARY KEY,
    first      TEXT NOT NULL,
    last       TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE paper_dividends (
    account   TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    symbol    TEXT    NOT NULL,
    ex_date   TEXT    NOT NULL,
    pay_date  TEXT    NOT NULL,
    pay_known INTEGER NOT NULL,
    shares    INTEGER NOT NULL,
    amount    REAL    NOT NULL,
    currency  TEXT    NOT NULL,
    rate      REAL    NOT NULL,
    cash      REAL    NOT NULL,
    PRIMARY KEY (account, source, symbol, ex_date)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
