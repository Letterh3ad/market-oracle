from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# A tie between two companies a document shows (a 10-K sentence or a headline naming both), typed
# by the keyword rule and by a model: `symbol` is the company, `other` what the text says it is to
# it. One row per (company, other, document, model), so reading the same document again with the
# same model changes nothing. `state`: linked (kept as a company link), unconfirmed (shown for the
# user to confirm or delete), none (no tie), confirmed or dismissed (the user's call).
# `edge_reads` marks each document read for a company, so a filing is fetched and read once.
DDL = """
CREATE TABLE link_candidates (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL COLLATE NOCASE,
    other       TEXT NOT NULL COLLATE NOCASE,
    kind        TEXT NOT NULL,
    article_id  INTEGER NOT NULL REFERENCES articles (id),
    evidence    TEXT NOT NULL,
    known_at    TEXT NOT NULL,
    rule        TEXT NOT NULL,
    model       TEXT NOT NULL,
    choice      TEXT NOT NULL,
    prob        REAL NOT NULL,
    relation    TEXT NOT NULL,
    state       TEXT NOT NULL,
    typed_at    TEXT NOT NULL,
    decided_at  TEXT,
    UNIQUE (symbol, other, article_id, model)
);
CREATE INDEX link_candidates_symbol ON link_candidates (symbol, state);
CREATE INDEX link_candidates_other ON link_candidates (other, state);
CREATE TABLE edge_reads (
    symbol     TEXT NOT NULL COLLATE NOCASE,
    article_id INTEGER NOT NULL REFERENCES articles (id),
    model      TEXT NOT NULL,
    read_at    TEXT NOT NULL,
    found      INTEGER NOT NULL,
    PRIMARY KEY (symbol, article_id, model)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
