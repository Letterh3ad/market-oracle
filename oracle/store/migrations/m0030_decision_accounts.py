from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# The AI account logs its decisions beside the strategy's, each with the research run whose
# verdict it traded. Rows from before were all the strategy's.
DDL = """
ALTER TABLE strategy_decisions ADD COLUMN account TEXT NOT NULL DEFAULT 'strategy';
ALTER TABLE strategy_decisions ADD COLUMN report_id INTEGER REFERENCES research_runs (id);
CREATE INDEX strategy_decisions_account ON strategy_decisions (account, id);
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
