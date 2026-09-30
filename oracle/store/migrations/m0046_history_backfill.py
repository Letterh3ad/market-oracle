from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The history backfill: each window it finished per company and source (a restart skips those),
# which company each article it kept was found for (one article, several companies), and the
# names the user set to search a company's news by. Stored filings are found by ticker without
# scanning every article.
DDL = """
CREATE TABLE history_windows (
    symbol   TEXT    NOT NULL COLLATE NOCASE,
    source   TEXT    NOT NULL,
    start    TEXT    NOT NULL,
    end      TEXT    NOT NULL,
    found    INTEGER NOT NULL,
    kept     INTEGER NOT NULL,
    undated  INTEGER NOT NULL,
    outside  INTEGER NOT NULL,
    on_topic INTEGER,
    note     TEXT,
    done_at  TEXT    NOT NULL,
    PRIMARY KEY (symbol, source, start, end)
) WITHOUT ROWID;
CREATE TABLE history_articles (
    symbol     TEXT    NOT NULL COLLATE NOCASE,
    article_id INTEGER NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
    PRIMARY KEY (symbol, article_id)
) WITHOUT ROWID;
CREATE INDEX history_articles_article ON history_articles (article_id);
CREATE TABLE history_names (
    symbol  TEXT    PRIMARY KEY COLLATE NOCASE,
    query   TEXT    NOT NULL,
    aliases TEXT    NOT NULL,
    skip    INTEGER NOT NULL,
    set_at  TEXT    NOT NULL
) WITHOUT ROWID;
CREATE INDEX articles_filings ON articles (query COLLATE NOCASE) WHERE source = 'sec_edgar';
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
