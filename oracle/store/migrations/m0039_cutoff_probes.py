from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Cutoff probe (replay-study 01). Numbered 39 beside replay-study 02's 38, built in parallel:
# the branch merged second renumbers if needed. One probe per model (provider and model id), a
# new one replacing it, with every question, reply and mark so the result can be inspected.
DDL = """
CREATE TABLE cutoff_probes (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    profile TEXT NOT NULL,
    question_set TEXT NOT NULL,
    claimed TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    probed_at TEXT NOT NULL,
    UNIQUE (provider, model)
);
CREATE TABLE cutoff_answers (
    probe_id INTEGER NOT NULL REFERENCES cutoff_probes(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    day TEXT NOT NULL,
    question TEXT NOT NULL,
    expected TEXT NOT NULL,
    reply TEXT NOT NULL,
    correct INTEGER NOT NULL,
    PRIMARY KEY (probe_id, n)
);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
