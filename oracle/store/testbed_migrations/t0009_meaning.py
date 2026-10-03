from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# research-lab 04. One vector per method and embedding model, float32 little-endian; text_hash names the
# text it was made from, so an edited definition is embedded again. near_fail keeps the FAILed method a
# registration was near when it was registered, with the score and how it was scored.
DDL = """
CREATE TABLE lit_embeddings (
    method_id INTEGER NOT NULL REFERENCES lit_methods(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector    BLOB NOT NULL,
    at        TEXT NOT NULL,
    PRIMARY KEY (method_id, model)
);
ALTER TABLE lab_tests ADD COLUMN near_fail TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
