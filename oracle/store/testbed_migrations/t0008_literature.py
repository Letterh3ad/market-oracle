from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# research-lab 03. test_key names a test in research-lab 01's registry; no foreign key, so either
# ticket can land first (join on the registry's test name once both are in).
DDL = """
CREATE TABLE lit_sources (
    id       INTEGER PRIMARY KEY,
    title    TEXT NOT NULL,
    also_titled TEXT NOT NULL DEFAULT '',
    authors  TEXT NOT NULL DEFAULT '',
    year     INTEGER,
    quality  TEXT NOT NULL CHECK (quality IN
             ('replicated', 'peer-reviewed', 'working-paper', 'practitioner', 'abstract-only')),
    url      TEXT,
    read_on  TEXT,
    note     TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    added_by TEXT NOT NULL
);
CREATE TABLE lit_source_ids (
    key       TEXT PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES lit_sources(id) ON DELETE CASCADE
);
CREATE INDEX lit_source_ids_source ON lit_source_ids(source_id);
CREATE TABLE lit_methods (
    id             INTEGER PRIMARY KEY,
    ref            TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    family         TEXT NOT NULL DEFAULT '',
    definition     TEXT NOT NULL DEFAULT '',
    aliases        TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL CHECK (status IN
                   ('new', 'proposed', 'go', 'dropped', 'triage', 'power', 'built', 'train', 'confirm',
                    'pass', 'fail', 'promising', 'not-testable', 'duplicate-of')),
    duplicate_of   INTEGER REFERENCES lit_methods(id),
    origin         TEXT NOT NULL,
    rating         INTEGER,
    archive_status TEXT NOT NULL DEFAULT '',
    archive_note   TEXT NOT NULL DEFAULT '',
    evidence       TEXT NOT NULL DEFAULT '',
    why            TEXT NOT NULL DEFAULT '',
    result         TEXT NOT NULL DEFAULT '',
    see_also       TEXT NOT NULL DEFAULT '[]',
    test_key       TEXT,
    status_by      TEXT NOT NULL,
    status_at      TEXT NOT NULL,
    added_at       TEXT NOT NULL
);
CREATE INDEX lit_methods_test ON lit_methods(test_key);
CREATE TABLE lit_method_sources (
    method_id INTEGER NOT NULL REFERENCES lit_methods(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES lit_sources(id) ON DELETE CASCADE,
    PRIMARY KEY (method_id, source_id)
);
CREATE INDEX lit_method_sources_source ON lit_method_sources(source_id);
CREATE TABLE lit_marks (
    id          INTEGER PRIMARY KEY,
    method_id   INTEGER NOT NULL REFERENCES lit_methods(id) ON DELETE CASCADE,
    mark        TEXT NOT NULL CHECK (mark IN ('go', 'drop', 'dig')),
    status_from TEXT NOT NULL,
    status_to   TEXT NOT NULL,
    by          TEXT NOT NULL,
    at          TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX lit_marks_method ON lit_marks(method_id);
CREATE TABLE lit_searches (
    id    INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    place TEXT NOT NULL,
    at    TEXT NOT NULL,
    by    TEXT NOT NULL,
    note  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE lit_search_sources (
    search_id INTEGER NOT NULL REFERENCES lit_searches(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES lit_sources(id) ON DELETE CASCADE,
    PRIMARY KEY (search_id, source_id)
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
