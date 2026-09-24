from __future__ import annotations

import sqlite3

from oracle.store.migrations import run_ddl

# AI decisions say whether researching the listing would let the account act, so the page can
# offer a button without reading the reason. Older rows are matched on the reasons written then.
DDL = """
ALTER TABLE strategy_decisions ADD COLUMN needs_research INTEGER NOT NULL DEFAULT 0;
UPDATE strategy_decisions SET needs_research = 1
 WHERE account = 'ai' AND action = 'skip'
   AND (reason = 'no verdict yet: run research on it' OR reason LIKE '%: run research on it again');
"""


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
