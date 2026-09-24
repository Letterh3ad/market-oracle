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
    profile: str | None = None
    """The model profile it was asked to run on, if one was picked."""


class JobHistoryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, kind: str, label: str, subject: str | None, link: str | None, state: str,
            submitted_at: datetime, pid: int, profile: str | None = None) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO jobs (kind, label, subject, link, state, submitted_at, pid, profile) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, label, subject, link, state, to_utc_iso(submitted_at), pid, profile),
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

    def finished(self, limit: int, offset: int = 0, search: str = "") -> list[JobRow]:
        """Newest first; `search` matches label, kind, state or error, ignoring case."""
        where, args = _finished_where(search)
        rows = self._conn.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY finished_at DESC, id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        return [_row(r) for r in rows]

    def count_finished(self, search: str = "") -> int:
        where, args = _finished_where(search)
        return self._conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", args).fetchone()[0]

    def get(self, job_id: int) -> JobRow | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row(row) if row else None

    def latest_per_subject(self) -> list[JobRow]:
        """The newest finished job for each kind and subject, newest first: whether that one
        needs retrying tells the whole story, since anything before it already ran again."""
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE finished_at IS NOT NULL AND subject IS NOT NULL "
            "ORDER BY finished_at DESC, id DESC"
        )
        seen: set[tuple[str, str]] = set()
        out = []
        for r in rows:
            key = (r["kind"], r["subject"])
            if key in seen:
                continue
            seen.add(key)
            out.append(_row(r))
        return out


def _finished_where(search: str) -> tuple[str, tuple]:
    search = search.strip()
    if not search:
        return "finished_at IS NOT NULL", ()
    pattern = "%" + search.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    fields = " OR ".join(f"{f} LIKE ? ESCAPE '!'" for f in ("label", "kind", "state", "error"))
    return f"finished_at IS NOT NULL AND ({fields})", (pattern,) * 4


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row(r) -> JobRow:
    return JobRow(r["id"], r["kind"], r["label"], r["subject"], r["link"], r["state"], r["error"],
                  datetime.fromisoformat(r["submitted_at"]), _when(r["started_at"]), _when(r["finished_at"]),
                  r["profile"])
