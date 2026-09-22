from __future__ import annotations

import sqlite3

from oracle.store.db import transaction
from oracle.store.migrations import MIGRATIONS

SCHEMA_VERSION = len(MIGRATIONS)


class SchemaTooNew(RuntimeError):
    pass


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    version = current_version(conn)
    head = len(MIGRATIONS)
    if version > head:
        raise SchemaTooNew(
            f"database schema version {version} is newer than this build supports "
            f"({head}); upgrade market-oracle instead of downgrading the data"
        )
    for index, module in enumerate(MIGRATIONS, start=1):
        if index <= version:
            continue
        with transaction(conn):
            module.up(conn)
            conn.execute(f"PRAGMA user_version={index}")
    return head
