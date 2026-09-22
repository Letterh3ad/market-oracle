from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from oracle.clock import Clock, SystemClock
from oracle.config import Config
from oracle.services.errors import DatabaseTooNew
from oracle.store.db import connect, to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.migrate import SchemaTooNew, migrate


@dataclass
class AppContext:
    config: Config
    config_source: Path | None
    conn: sqlite3.Connection
    clock: Clock


@contextmanager
def open_context(
    config: Config, config_source: Path | None, clock: Clock | None = None
) -> Iterator[AppContext]:
    clock = clock or SystemClock()
    conn = connect(config.db_path)
    try:
        try:
            migrate(conn)
        except SchemaTooNew as exc:
            raise DatabaseTooNew(str(exc)) from exc
        MetaRepo(conn).setdefault("created_at", to_utc_iso(clock.utcnow()))
        yield AppContext(config, config_source, conn, clock)
    finally:
        conn.close()
