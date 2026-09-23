from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from oracle.store.db import to_utc_iso, transaction

NOT_RUN = "not run: app closed"
INTERRUPTED = "interrupted: app closed"


@dataclass(frozen=True)
class JobRow:
    id: int
    kind: str
    label: str
    subject: str | None
    link: str | None
    state: str
    error: str | None
    submitted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobHistoryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, kind: str, label: str, subject: str | None, link: str | None, state: str,
            submitted_at: datetime, pid: int) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO jobs (kind, label, subject, link, state, submitted_at, pid) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, label, subject, link, state, to_utc_iso(submitted_at), pid),
            )
        return int(cur.lastrowid)

    def update(self, job_id: int, state: str, error: str | None, started_at: datetime | None,
               finished_at: datetime | None) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE jobs SET state = ?, error = ?, started_at = ?, finished_at = ? WHERE id = ?",
                (state, error, started_at and to_utc_iso(started_at), finished_at and to_utc_iso(finished_at),
                 job_id),
            )

    def sweep(self, at: datetime, dead: Callable[[int | None], bool]) -> None:
        """Close what a stopped server left behind (its pid is `dead`): queued never ran,
        running was cut off. A live server's jobs are its own to finish."""
        with transaction(self._conn):
            rows = self._conn.execute("SELECT id, state, pid FROM jobs WHERE state IN ('queued', 'running')")
            for job_id, state, pid in rows.fetchall():
                if dead(pid):
                    closed = ("cancelled", NOT_RUN) if state == "queued" else ("interrupted", INTERRUPTED)
                    self._conn.execute("UPDATE jobs SET state = ?, error = ?, finished_at = ? WHERE id = ?",
                                       (*closed, to_utc_iso(at), job_id))

    def finished(self, limit: int) -> list[JobRow]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [_row(r) for r in rows]

    def get(self, job_id: int) -> JobRow | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row(row) if row else None


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row(r) -> JobRow:
    return JobRow(r["id"], r["kind"], r["label"], r["subject"], r["link"], r["state"], r["error"],
                  datetime.fromisoformat(r["submitted_at"]), _when(r["started_at"]), _when(r["finished_at"]))
