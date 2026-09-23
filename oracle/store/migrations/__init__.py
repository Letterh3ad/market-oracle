from __future__ import annotations

import sqlite3


def run_ddl(conn: sqlite3.Connection, ddl: str) -> None:
    # executescript() issues its own implicit COMMIT, breaking atomicity against the
    # user_version bump in migrate(). Statements are flushed with
    # sqlite3.complete_statement rather than split on ";", which a trigger body or a
    # semicolon inside a literal would break.
    buf = ""
    for line in ddl.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statement = buf.strip()
            if statement:
                conn.execute(statement)
            buf = ""
    if buf.strip():
        raise ValueError(f"migration DDL ends mid-statement: {buf.strip()[:80]!r}")


from oracle.store.migrations import (  # noqa: E402
    m0001_meta,
    m0002_sources,
    m0003_job_locks,
    m0004_listings,
    m0005_listings_fts,
    m0006_fund_fields,
    m0007_tags,
    m0008_watchlists,
    m0009_bars,
    m0010_fx_rates,
    m0011_fundamentals,
    m0012_fx_history,
    m0013_signals,
    m0014_paper,
    m0015_paper_equity,
    m0016_leans,
    m0017_symbol_nocase,
    m0018_research,
    m0019_articles,
    m0020_research_as_of,
    m0021_sec_tickers,
)

MIGRATIONS = [
    m0001_meta,
    m0002_sources,
    m0003_job_locks,
    m0004_listings,
    m0005_listings_fts,
    m0006_fund_fields,
    m0007_tags,
    m0008_watchlists,
    m0009_bars,
    m0010_fx_rates,
    m0011_fundamentals,
    m0012_fx_history,
    m0013_signals,
    m0014_paper,
    m0015_paper_equity,
    m0016_leans,
    m0017_symbol_nocase,
    m0018_research,
    m0019_articles,
    m0020_research_as_of,
    m0021_sec_tickers,
]
