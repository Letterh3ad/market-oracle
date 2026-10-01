from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# xbrl_facts keeps every filing's value of a period (restatements too); readers pick the first
# filed. start is '' for an instant (a balance). xbrl_accepted holds acceptance times converted
# from SEC's UTC stamp to New York time (YYYY-MM-DDTHH:MM).
DDL = """
CREATE TABLE xbrl_tickers (
    ticker     TEXT PRIMARY KEY,
    cik        INTEGER NOT NULL,
    name       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE xbrl_members (
    ticker          TEXT PRIMARY KEY,
    cik             INTEGER,
    facts_tried_at  TEXT,
    facts_note      TEXT,
    splits_tried_at TEXT
) WITHOUT ROWID;

CREATE TABLE xbrl_facts (
    cik       INTEGER NOT NULL,
    concept   TEXT    NOT NULL,
    unit      TEXT    NOT NULL,
    start     TEXT    NOT NULL,
    end       TEXT    NOT NULL,
    value     REAL    NOT NULL,
    accession TEXT    NOT NULL,
    form      TEXT    NOT NULL,
    filed     TEXT    NOT NULL,
    PRIMARY KEY (cik, concept, unit, start, end, accession)
) WITHOUT ROWID;

CREATE TABLE xbrl_accepted (
    accession TEXT PRIMARY KEY,
    cik       INTEGER NOT NULL,
    accepted  TEXT    NOT NULL
) WITHOUT ROWID;
CREATE INDEX xbrl_accepted_cik ON xbrl_accepted (cik);

CREATE TABLE tb_splits (
    ticker TEXT NOT NULL,
    day    TEXT NOT NULL,
    ratio  REAL NOT NULL,
    PRIMARY KEY (ticker, day)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
