from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Daily industry research. `industry_news`: the headlines a company's own recent search found
# (the company is the article's subject). `industry_fetches`: when each company was last searched,
# so a run soon after another asks again only for stale ones. `industry_pairs`: for a target
# company, which company's search brought each article it had scored (own articles have the
# target itself). `laya_daily`: the per company, day and relation features, a stored copy of the
# aggregator's answer (day = when the news was known, UTC); `events` is a JSON map of event type
# to count, `event_scores` of event type to the signed sum of its articles.
DDL = """
CREATE TABLE industry_news (
    symbol     TEXT NOT NULL COLLATE NOCASE,
    article_id INTEGER NOT NULL REFERENCES articles (id),
    found_at   TEXT NOT NULL,
    PRIMARY KEY (symbol, article_id)
) WITHOUT ROWID;
CREATE TABLE industry_fetches (
    symbol     TEXT PRIMARY KEY COLLATE NOCASE,
    fetched_at TEXT NOT NULL,
    found      INTEGER NOT NULL,
    kept       INTEGER NOT NULL
);
CREATE TABLE industry_pairs (
    symbol     TEXT NOT NULL COLLATE NOCASE,
    article_id INTEGER NOT NULL REFERENCES articles (id),
    subject    TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (symbol, article_id)
) WITHOUT ROWID;
CREATE TABLE laya_daily (
    symbol     TEXT NOT NULL COLLATE NOCASE,
    model      TEXT NOT NULL,
    day        TEXT NOT NULL,
    relation   TEXT NOT NULL,
    articles   INTEGER NOT NULL,
    score      REAL NOT NULL,
    events     TEXT NOT NULL,
    event_scores TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, model, day, relation)
) WITHOUT ROWID;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
