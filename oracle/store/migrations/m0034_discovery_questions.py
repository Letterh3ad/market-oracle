from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Discovery chat. A follow-up is a question of its own that `refines` another, so each keeps
# its own answers. Every answer keeps its whole shortlist, skipped names with why included, so
# a later rerun can say what changed. Evidence ids are a JSON list: they are only ever read with
# their item.
DDL = """
CREATE TABLE discovery_questions (
    id       INTEGER PRIMARY KEY,
    text     TEXT NOT NULL,
    refines  INTEGER REFERENCES discovery_questions (id),
    asked_at TEXT NOT NULL
);
CREATE TABLE discovery_answers (
    id          INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES discovery_questions (id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    stage       TEXT,
    error       TEXT,
    searches    TEXT NOT NULL DEFAULT '[]',
    notes       TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX discovery_answers_question ON discovery_answers (question_id);
CREATE TABLE discovery_answer_items (
    answer_id   INTEGER NOT NULL REFERENCES discovery_answers (id) ON DELETE CASCADE,
    n           INTEGER NOT NULL,
    name        TEXT NOT NULL,
    symbol      TEXT,
    company_key TEXT,
    company     TEXT,
    why         TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    skipped     TEXT,
    PRIMARY KEY (answer_id, n)
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
