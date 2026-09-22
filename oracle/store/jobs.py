from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class LockRow:
    job: str
    token: str
    pid: int
    host: str
    acquired_at: float
    heartbeat_at: float
    lease_s: float


class JobLockRepo:
    """Row access only. Callers hold the transaction so check-then-take is atomic."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, job: str) -> LockRow | None:
        row = self._conn.execute("SELECT * FROM job_locks WHERE job = ?", (job,)).fetchone()
        return LockRow(**dict(row)) if row else None

    def all(self) -> list[LockRow]:
        return [LockRow(**dict(r)) for r in self._conn.execute("SELECT * FROM job_locks ORDER BY job")]

    def put(self, row: LockRow) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO job_locks (job, token, pid, host, acquired_at, heartbeat_at, "
            "lease_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row.job, row.token, row.pid, row.host, row.acquired_at, row.heartbeat_at, row.lease_s),
        )

    def touch(self, job: str, token: str, at: float) -> bool:
        cur = self._conn.execute(
            "UPDATE job_locks SET heartbeat_at = ? WHERE job = ? AND token = ?", (at, job, token)
        )
        return cur.rowcount == 1

    def delete(self, job: str, token: str) -> None:
        self._conn.execute("DELETE FROM job_locks WHERE job = ? AND token = ?", (job, token))
