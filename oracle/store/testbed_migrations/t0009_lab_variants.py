from __future__ import annotations

import sqlite3

# variants: how many versions of a strategy were tried before its registration, the deflated
# Sharpe's trial count (research-lab 02). 1 for every test registered before it, the trend overlay
# included (one rule, never tuned).
def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE lab_tests ADD COLUMN variants INTEGER NOT NULL DEFAULT 1")
