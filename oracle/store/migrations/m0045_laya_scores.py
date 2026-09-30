from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Laya's reading of one article for one target company, one row per model tag: the same pair
# and model is never scored twice, and a new checkpoint scores under its own tag. `relation` is
# what the article's subject is to the target (own, competitor, supplier, customer, industry).
# Search snippets, first seen, so Laya reads headline and snippet only.
DDL = """
CREATE TABLE laya_scores (
    article_id      INTEGER NOT NULL REFERENCES articles (id),
    symbol          TEXT NOT NULL COLLATE NOCASE,
    model           TEXT NOT NULL,
    relation        TEXT NOT NULL,
    relevant        REAL NOT NULL,
    direction       TEXT NOT NULL,
    direction_probs TEXT NOT NULL,
    event           TEXT NOT NULL,
    event_prob      REAL NOT NULL,
    scored_at       TEXT NOT NULL,
    PRIMARY KEY (article_id, symbol, model)
) WITHOUT ROWID;
CREATE INDEX laya_scores_symbol ON laya_scores (symbol, model);
ALTER TABLE articles ADD COLUMN snippet TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
