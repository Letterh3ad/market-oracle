from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Model slots (models 04). Each research stage's profile and model, JSON since it is only ever
# read whole; and the profile a job was asked to run on, so a queued job or a retry keeps it.
DDL = """
ALTER TABLE research_runs ADD COLUMN stages TEXT;
ALTER TABLE jobs ADD COLUMN profile TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
