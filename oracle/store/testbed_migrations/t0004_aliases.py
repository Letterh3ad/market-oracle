from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# tb_aliases: renames checked against SEC and Yahoo; only accepted rows price anything, the rest
# are kept for review. `new` is the last live ticker of a chain (SYMC -> NLOK -> GEN is GEN).
# tb_members.identity: whether today's holder of a former member's ticker is that member, by SEC.
DDL = """
CREATE TABLE tb_aliases (
    old           TEXT    NOT NULL,
    new           TEXT    NOT NULL,
    effective_day TEXT,
    accepted      INTEGER NOT NULL,
    evidence      TEXT    NOT NULL,
    verified_at   TEXT    NOT NULL,
    priced_to     TEXT,
    PRIMARY KEY (old, new)
) WITHOUT ROWID;

ALTER TABLE tb_members ADD COLUMN cik INTEGER;
ALTER TABLE tb_members ADD COLUMN identity TEXT;
ALTER TABLE tb_members ADD COLUMN identity_note TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
