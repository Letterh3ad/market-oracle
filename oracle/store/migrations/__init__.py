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


from oracle.store.migrations import m0001_meta, m0002_sources, m0003_job_locks  # noqa: E402

MIGRATIONS = [m0001_meta, m0002_sources, m0003_job_locks]
