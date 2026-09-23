from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator

import httpx

from oracle.clock import Clock, SystemClock
from oracle.config import Config, ConfigError, load_config
from oracle.services.errors import ConfigInvalid, DatabaseTooNew
from oracle.store.db import connect, to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.migrate import SchemaTooNew, migrate


@dataclass
class AppContext:
    config: Config
    config_source: Path | None
    conn: sqlite3.Connection
    clock: Clock
    # None means the real network; tests pass an httpx.MockTransport.
    transport: httpx.AsyncBaseTransport | None = None


def load_settings(path: Path | None) -> tuple[Config, Path | None]:
    try:
        return load_config(path)
    except ConfigError as exc:
        raise ConfigInvalid(str(exc)) from exc


@contextmanager
def open_context(
    config: Config,
    config_source: Path | None,
    clock: Clock | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Iterator[AppContext]:
    clock = clock or SystemClock()
    conn = connect(config.db_path)
    try:
        try:
            migrate(conn)
        except SchemaTooNew as exc:
            raise DatabaseTooNew(str(exc)) from exc
        MetaRepo(conn).setdefault("created_at", to_utc_iso(clock.utcnow()))
        yield AppContext(config, config_source, conn, clock, transport)
    finally:
        conn.close()


@contextmanager
def worker_context(ctx: AppContext) -> Iterator[AppContext]:
    """`ctx` over a connection of its own, for work on another thread: a SQLite connection
    stays on the thread that opened it."""
    conn = connect(ctx.config.db_path)
    try:
        yield replace(ctx, conn=conn)
    finally:
        conn.close()
