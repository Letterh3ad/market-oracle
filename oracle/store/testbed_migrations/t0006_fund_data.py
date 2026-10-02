from __future__ import annotations

import sqlite3

from oracle.store.migrations import m0010_fx_rates, m0012_fx_history, m0050_dividends, run_ddl

# The fund comparison (boring-plan 02) converts US-listed proxies to sterling and reinvests their
# dividends from the research database, so it carries the live schema's FX and dividend tables.


def up(conn: sqlite3.Connection) -> None:
    m0010_fx_rates.up(conn)
    m0012_fx_history.up(conn)
    run_ddl(conn, m0050_dividends.DIVIDENDS_DDL)
