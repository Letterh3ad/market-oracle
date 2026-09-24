from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# Auto questions (discovery 05). A question's switch; each shortlisted company's validation
# outcome on its answer; and on a company auto-added, the question and the checks it passed, so
# the discovered list can say where it came from. Validations are JSON: only ever read whole.
DDL = """
ALTER TABLE discovery_questions ADD COLUMN auto INTEGER NOT NULL DEFAULT 0;
ALTER TABLE discovery_answer_items ADD COLUMN validation TEXT;
ALTER TABLE discoveries ADD COLUMN question_id INTEGER REFERENCES discovery_questions (id);
ALTER TABLE discoveries ADD COLUMN validation TEXT;
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
