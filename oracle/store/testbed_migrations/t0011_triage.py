from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# research-lab 05. Open Source Asset Pricing (Chen and Zimmermann): the signal documentation and the
# long-short portfolios' monthly returns in percent, "op" as the original paper built them and "large"
# with the ME above the NYSE 20th percentile screen. lab_osap_map ties an index method to its OSAP
# signal; lab_stages keeps every funnel stage run (triage, power) with the status it moved the method to.
DDL = """
CREATE TABLE osap_signals (
    signal       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    authors      TEXT NOT NULL DEFAULT '',
    year         INTEGER,
    journal      TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    sample_start INTEGER,
    sample_end   INTEGER,
    sign         REAL,
    paper_return REAL,
    paper_t      REAL,
    weight       TEXT NOT NULL DEFAULT '',
    quantile     REAL,
    release      TEXT NOT NULL
);
CREATE TABLE osap_returns (
    signal  TEXT NOT NULL,
    variant TEXT NOT NULL CHECK (variant IN ('op', 'large')),
    month   TEXT NOT NULL,
    ret     REAL NOT NULL,
    PRIMARY KEY (signal, variant, month)
) WITHOUT ROWID;
CREATE TABLE osap_imports (
    id      INTEGER PRIMARY KEY,
    what    TEXT NOT NULL,
    release TEXT NOT NULL,
    file    TEXT NOT NULL,
    rows    INTEGER NOT NULL,
    at      TEXT NOT NULL
);
CREATE TABLE lab_osap_map (
    method_id INTEGER PRIMARY KEY REFERENCES lit_methods(id) ON DELETE CASCADE,
    signal    TEXT NOT NULL,
    by        TEXT NOT NULL,
    at        TEXT NOT NULL
);
CREATE TABLE lab_stages (
    id          INTEGER PRIMARY KEY,
    method_id   INTEGER NOT NULL REFERENCES lit_methods(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL CHECK (stage IN ('triage', 'power')),
    outcome     TEXT NOT NULL CHECK (outcome IN ('pass', 'stop')),
    summary     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    status_from TEXT NOT NULL,
    status_to   TEXT NOT NULL,
    by          TEXT NOT NULL,
    at          TEXT NOT NULL
);
CREATE INDEX lab_stages_method ON lab_stages(method_id);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
