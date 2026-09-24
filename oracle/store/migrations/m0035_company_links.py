from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# A link reads "other is <relation> of symbol". A deleted link keeps its row (deleted_at set)
# so the same pair is never recorded again. `via` names the link a planned research search
# came from; research_evidence.query is the search that showed an article to that run (the
# article's own query is the first ever, often another run's), so its evidence can say so.
DDL = """
CREATE TABLE company_links (
    id         INTEGER PRIMARY KEY,
    symbol     TEXT NOT NULL COLLATE NOCASE,
    other      TEXT NOT NULL COLLATE NOCASE,
    relation   TEXT NOT NULL,
    source     TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE (symbol, other, relation)
);
CREATE INDEX company_links_other ON company_links (other);
CREATE TABLE company_link_evidence (
    link_id    INTEGER NOT NULL REFERENCES company_links (id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles (id),
    PRIMARY KEY (link_id, article_id)
) WITHOUT ROWID;
ALTER TABLE research_queries ADD COLUMN via TEXT;
ALTER TABLE research_evidence ADD COLUMN query TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
