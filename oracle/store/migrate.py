from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from oracle.store.db import transaction
from oracle.store.migrations import MIGRATIONS

SCHEMA_VERSION = len(MIGRATIONS)


class SchemaTooNew(RuntimeError):
    pass


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, migrations: Sequence | None = None) -> int:
    """Bring `conn` to the head of `migrations` (the live database's by default; the research
    database has its own list)."""
    # Read at call time, not SCHEMA_VERSION, so tests can substitute MIGRATIONS.
    migrations = MIGRATIONS if migrations is None else migrations
    head = len(migrations)
    _refuse_newer(current_version(conn), head)
    for index, module in enumerate(migrations, start=1):
        with transaction(conn):
            # Re-read under the write lock: another process opening the same fresh
            # database may have applied this step since the check above.
            version = current_version(conn)
            _refuse_newer(version, head)
            if index <= version:
                continue
            module.up(conn)
            conn.execute(f"PRAGMA user_version={index}")
    return head


def _refuse_newer(version: int, head: int) -> None:
    if version > head:
        raise SchemaTooNew(
            f"database schema version {version} is newer than this build supports "
            f"({head}); upgrade market-oracle instead of downgrading the data"
        )
