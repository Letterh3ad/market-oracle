from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from oracle.store.db import to_utc_iso, transaction

PAUSED, RUNNING = "paused", "running"
PENDING, DONE, FAILED = "pending", "done", "failed"
"""Cell states, with RUNNING."""


@dataclass(frozen=True)
class StudyRow:
    id: int
    created_at: datetime
    state: str
    first_day: date
    last_day: date
    listings: str
    """JSON, as the service saved it."""
    paused_reason: str | None


@dataclass(frozen=True)
class CellRow:
    id: int
    study_id: int
    symbol: str
    as_of: date
    state: str
    position: int
    attempts: int
    job_id: int | None
    run_id: int | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


_CELL = ("id, study_id, symbol, as_of, state, position, attempts, job_id, run_id, error, started_at, "
         "finished_at")


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _cell(r) -> CellRow:
    return CellRow(r[0], r[1], r[2], date.fromisoformat(r[3]), r[4], r[5], r[6], r[7], r[8], r[9], _when(r[10]),
                   _when(r[11]))


class ReplayRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, created_at: datetime, first_day: date, last_day: date, listings: str,
               cells: Sequence[tuple[str, date]]) -> int:
        """A paused study with its cells queued in the order given."""
        with transaction(self._conn):
            study_id = self._conn.execute(
                "INSERT INTO replay_studies (created_at, state, first_day, last_day, listings) VALUES (?, ?, ?, ?, ?)",
                (to_utc_iso(created_at), PAUSED, first_day.isoformat(), last_day.isoformat(), listings),
            ).lastrowid
            self._conn.executemany(
                "INSERT INTO replay_cells (study_id, symbol, as_of, state, position) VALUES (?, ?, ?, ?, ?)",
                [(study_id, s, d.isoformat(), PENDING, n) for n, (s, d) in enumerate(cells)],
            )
        return int(study_id)

    def current(self) -> StudyRow | None:
        """The newest study: the one every command acts on."""
        r = self._conn.execute(
            "SELECT id, created_at, state, first_day, last_day, listings, paused_reason FROM replay_studies "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            return None
        return StudyRow(r[0], datetime.fromisoformat(r[1]), r[2], date.fromisoformat(r[3]), date.fromisoformat(r[4]),
                        r[5], r[6])

    def set_state(self, study_id: int, state: str, reason: str | None = None) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE replay_studies SET state = ?, paused_reason = ? WHERE id = ?",
                               (state, reason, study_id))

    def cells(self, study_id: int, state: str | None = None) -> list[CellRow]:
        """In queue order."""
        rows = self._conn.execute(
            f"SELECT {_CELL} FROM replay_cells WHERE study_id = ? AND (? IS NULL OR state = ?) ORDER BY position, id",
            (study_id, state, state))
        return [_cell(r) for r in rows]

    def cell(self, cell_id: int) -> CellRow | None:
        r = self._conn.execute(f"SELECT {_CELL} FROM replay_cells WHERE id = ?", (cell_id,)).fetchone()
        return _cell(r) if r else None

    def counts(self, study_id: int) -> dict[str, int]:
        rows = self._conn.execute("SELECT state, COUNT(*) FROM replay_cells WHERE study_id = ? GROUP BY state",
                                  (study_id,))
        return {r[0]: r[1] for r in rows}

    def next_pending(self, study_id: int) -> CellRow | None:
        r = self._conn.execute(
            f"SELECT {_CELL} FROM replay_cells WHERE study_id = ? AND state = ? ORDER BY position, id LIMIT 1",
            (study_id, PENDING)).fetchone()
        return _cell(r) if r else None

    def mark_running(self, cell_id: int, job_id: int, at: datetime) -> None:
        self._set(cell_id, "state = ?, job_id = ?, started_at = ?, finished_at = NULL",
                  RUNNING, job_id, to_utc_iso(at))

    def mark_done(self, cell_id: int, run_id: int | None, at: datetime) -> None:
        self._set(cell_id, "state = ?, run_id = ?, error = NULL, attempts = attempts + 1, finished_at = ?",
                  DONE, run_id, to_utc_iso(at))

    def mark_failed(self, cell_id: int, error: str, at: datetime, requeue: bool) -> None:
        """Counts an attempt and keeps the error; with `requeue`, back to pending at the end of the queue."""
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE replay_cells SET state = ?, error = ?, attempts = attempts + 1, finished_at = ?, "
                "position = CASE WHEN ? THEN (SELECT MAX(position) + 1 FROM replay_cells c "
                "WHERE c.study_id = replay_cells.study_id) ELSE position END WHERE id = ?",
                (PENDING if requeue else FAILED, error, to_utc_iso(at), requeue, cell_id))

    def back_to_pending(self, cell_id: int) -> None:
        """Cut off before it could finish: no attempt counted, its place kept."""
        self._set(cell_id, "state = ?, job_id = NULL, started_at = NULL", PENDING)

    def retry_failed(self, study_id: int, cell_id: int | None = None) -> int:
        """Failed cells (or the one) back to pending at the end of the queue, with one attempt
        left: the automatic retry is not given again. The count moved."""
        with transaction(self._conn):
            end = self._conn.execute("SELECT COALESCE(MAX(position), 0) FROM replay_cells WHERE study_id = ?",
                                     (study_id,)).fetchone()[0]
            ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM replay_cells WHERE study_id = ? AND state = ? AND (? IS NULL OR id = ?) "
                "ORDER BY position, id", (study_id, FAILED, cell_id, cell_id))]
            self._conn.executemany("UPDATE replay_cells SET state = ?, attempts = 1, position = ? WHERE id = ?",
                                   [(PENDING, end + n, i) for n, i in enumerate(ids, start=1)])
        return len(ids)

    def durations(self, study_id: int, limit: int) -> list[float]:
        """Seconds each of the latest `limit` done cells took, newest first."""
        rows = self._conn.execute(
            "SELECT started_at, finished_at FROM replay_cells WHERE study_id = ? AND state = ? "
            "AND started_at IS NOT NULL ORDER BY finished_at DESC LIMIT ?", (study_id, DONE, limit))
        return [(datetime.fromisoformat(r[1]) - datetime.fromisoformat(r[0])).total_seconds() for r in rows]

    def failure_reasons(self, study_id: int, limit: int) -> list[tuple[str, int]]:
        """The commonest errors on cells still failed or waiting for their retry."""
        rows = self._conn.execute(
            "SELECT error, COUNT(*) AS n FROM replay_cells WHERE study_id = ? AND error IS NOT NULL "
            "AND state IN (?, ?) GROUP BY error ORDER BY n DESC, error LIMIT ?", (study_id, FAILED, PENDING, limit))
        return [(r[0], r[1]) for r in rows]

    def _set(self, cell_id: int, assignments: str, *values) -> None:
        with transaction(self._conn):
            self._conn.execute(f"UPDATE replay_cells SET {assignments} WHERE id = ?", (*values, cell_id))
