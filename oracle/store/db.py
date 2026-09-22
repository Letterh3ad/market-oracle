from __future__ import annotations

import itertools
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BUSY_TIMEOUT_S = 30.0


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    if str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    _enable_wal(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    # Switching a fresh database to WAL needs an exclusive lock, and SQLite can report
    # "locked" immediately instead of waiting on the busy handler, so several processes
    # opening a new database at once need their own retry.
    deadline = time.monotonic() + BUSY_TIMEOUT_S
    delay = 0.01
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


_savepoints = itertools.count()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Atomic block, reentrant so a caller can span several repository writes.

    Nested blocks become savepoints, since every repository write method opens its
    own transaction and SQLite has no nested BEGIN.
    """
    if conn.in_transaction:
        name = f"oracle_sp_{next(_savepoints)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return

    # IMMEDIATE, not DEFERRED: the daemon and the CLI share one WAL database, and a
    # deferred transaction that reads before writing fails its write upgrade with
    # SQLITE_BUSY_SNAPSHOT, which the busy timeout cannot resolve.
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    # Unconditional: a migration that reaches for executescript() (which issues its
    # own implicit COMMIT) must fail loudly here rather than silently losing
    # atomicity against the user_version bump.
    conn.execute("COMMIT")


def to_utc_iso(ts: datetime) -> str:
    """Normalize to a UTC ISO 8601 string so stored order matches chronological order.

    Raises on a naive datetime instead of assuming a timezone: guessing is how the
    wrong instant gets stored silently.
    """
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError(f"to_utc_iso requires an aware datetime, got naive: {ts!r}")
    return ts.astimezone(timezone.utc).isoformat()
