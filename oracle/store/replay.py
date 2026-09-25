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
    polled_at: datetime | None = None
    waiting: str | None = None
    stages: str | None = None
    """JSON: each research stage and the profile it ran on at creation."""
    pacer: str | None = None
    """JSON: what the pacer keeps between polls, as its last poll left it."""


@dataclass(frozen=True)
class CellRow:
    id: int
    study_id: int
    symbol: str
    as_of: date
    state: str
    position: int
    attempts: int
    allowed: int
    refunds: int
    job_id: int | None
    run_id: int | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


_STUDY = "id, created_at, state, first_day, last_day, listings, paused_reason, polled_at, waiting, stages, pacer"
_CELL = ("id, study_id, symbol, as_of, state, position, attempts, allowed, refunds, job_id, run_id, error, "
         "started_at, finished_at")


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _cell(r) -> CellRow:
    return CellRow(r[0], r[1], r[2], date.fromisoformat(r[3]), r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11],
                   _when(r[12]), _when(r[13]))


class ReplayRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, created_at: datetime, first_day: date, last_day: date, listings: str,
               cells: Sequence[tuple[str, date]], allowed: int, stages: str | None = None) -> int:
        """A paused study with its cells queued in the order given."""
        with transaction(self._conn):
            study_id = self._conn.execute(
                "INSERT INTO replay_studies (created_at, state, first_day, last_day, listings, stages) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (to_utc_iso(created_at), PAUSED, first_day.isoformat(), last_day.isoformat(), listings, stages),
            ).lastrowid
            self._insert_cells(study_id, cells, allowed)
        return int(study_id)

    def replace_cells(self, study_id: int, first_day: date, last_day: date, listings: str,
                      cells: Sequence[tuple[str, date]], allowed: int) -> None:
        """New listings and cells for a study none of whose cells has run."""
        with transaction(self._conn):
            self._conn.execute("DELETE FROM replay_cells WHERE study_id = ?", (study_id,))
            self._conn.execute("UPDATE replay_studies SET first_day = ?, last_day = ?, listings = ? WHERE id = ?",
                               (first_day.isoformat(), last_day.isoformat(), listings, study_id))
            self._insert_cells(study_id, cells, allowed)

    def _insert_cells(self, study_id: int, cells: Sequence[tuple[str, date]], allowed: int) -> None:
        self._conn.executemany(
            "INSERT INTO replay_cells (study_id, symbol, as_of, state, position, allowed) VALUES (?, ?, ?, ?, ?, ?)",
            [(study_id, s, d.isoformat(), PENDING, n, allowed) for n, (s, d) in enumerate(cells)],
        )

    def current(self) -> StudyRow | None:
        """The newest study: the one every command acts on."""
        r = self._conn.execute(f"SELECT {_STUDY} FROM replay_studies ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            return None
        return StudyRow(r[0], datetime.fromisoformat(r[1]), r[2], date.fromisoformat(r[3]), date.fromisoformat(r[4]),
                        r[5], r[6], _when(r[7]), r[8], r[9], r[10])

    def set_state(self, study_id: int, state: str, reason: str | None = None) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE replay_studies SET state = ?, paused_reason = ? WHERE id = ?",
                               (state, reason, study_id))

    def note_poll(self, study_id: int, at: datetime, waiting: str | None, pacer: str | None = None) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE replay_studies SET polled_at = ?, waiting = ?, pacer = COALESCE(?, pacer) "
                               "WHERE id = ?", (to_utc_iso(at), waiting, pacer, study_id))

    def cells(self, study_id: int, state: str | None = None) -> list[CellRow]:
        """In queue order."""
        rows = self._conn.execute(
            f"SELECT {_CELL} FROM replay_cells WHERE study_id = ? AND (? IS NULL OR state = ?) ORDER BY position, id",
            (study_id, state, state))
        return [_cell(r) for r in rows]

    def cell(self, cell_id: int) -> CellRow | None:
        r = self._conn.execute(f"SELECT {_CELL} FROM replay_cells WHERE id = ?", (cell_id,)).fetchone()
        return _cell(r) if r else None

    def cell_at(self, study_id: int, symbol: str, as_of: date) -> CellRow | None:
        r = self._conn.execute(f"SELECT {_CELL} FROM replay_cells WHERE study_id = ? AND symbol = ? AND as_of = ?",
                               (study_id, symbol, as_of.isoformat())).fetchone()
        return _cell(r) if r else None

    def any_running(self) -> bool:
        """A cell of any study running."""
        return self._conn.execute("SELECT 1 FROM replay_cells WHERE state = ? LIMIT 1", (RUNNING,)).fetchone() is not None

    def untouched(self, study_id: int) -> bool:
        """No cell has been submitted yet."""
        return self._conn.execute(
            "SELECT 1 FROM replay_cells WHERE study_id = ? AND (state != ? OR attempts > 0 OR job_id IS NOT NULL) "
            "LIMIT 1", (study_id, PENDING)).fetchone() is None

    def counts(self, study_id: int) -> dict[str, int]:
        rows = self._conn.execute("SELECT state, COUNT(*) FROM replay_cells WHERE study_id = ? GROUP BY state",
                                  (study_id,))
        return {r[0]: r[1] for r in rows}

    def retrying(self, study_id: int) -> int:
        """Pending cells that failed before."""
        return self._conn.execute("SELECT COUNT(*) FROM replay_cells WHERE study_id = ? AND state = ? "
                                  "AND error IS NOT NULL", (study_id, PENDING)).fetchone()[0]

    def next_pending(self, study_id: int) -> CellRow | None:
        r = self._conn.execute(
            f"SELECT {_CELL} FROM replay_cells WHERE study_id = ? AND state = ? ORDER BY position, id LIMIT 1",
            (study_id, PENDING)).fetchone()
        return _cell(r) if r else None

    def mark_running(self, cell_id: int, job_id: int, at: datetime) -> None:
        self._set(cell_id, "state = ?, job_id = ?, started_at = ?, finished_at = NULL",
                  RUNNING, job_id, to_utc_iso(at))

    def adopt(self, cell_id: int, job_id: int, at: datetime) -> None:
        """A replay of the cell started outside the pacer (Activity's Retry): the cell follows
        that job, with room for one more attempt."""
        self._set(cell_id, "state = ?, job_id = ?, started_at = ?, finished_at = NULL, "
                  "allowed = MAX(allowed, attempts + 1), refunds = 0", RUNNING, job_id, to_utc_iso(at))

    def mark_done(self, cell_id: int, run_id: int | None, at: datetime, started_at: datetime | None = None) -> None:
        """`started_at`, when known, is when the job began running, so time queued is not replay time."""
        self._set(cell_id, "state = ?, run_id = ?, error = NULL, attempts = attempts + 1, finished_at = ?, "
                  "started_at = COALESCE(?, started_at)", DONE, run_id, to_utc_iso(at),
                  to_utc_iso(started_at) if started_at else None)

    def mark_failed(self, cell_id: int, error: str, at: datetime) -> None:
        """Counts an attempt and keeps the error; with attempts left, back to pending at the
        end of the queue, else failed."""
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE replay_cells SET error = ?, attempts = attempts + 1, finished_at = ?, "
                "state = CASE WHEN attempts + 1 < allowed THEN ? ELSE ? END, "
                "position = CASE WHEN attempts + 1 < allowed THEN (SELECT MAX(position) + 1 FROM replay_cells c "
                "WHERE c.study_id = replay_cells.study_id) ELSE position END WHERE id = ?",
                (error, to_utc_iso(at), PENDING, FAILED, cell_id))

    def stop_for_good(self, cell_ids: Sequence[int], error: str, at: datetime) -> None:
        """Failed with no attempt left, so neither the pacer nor a retry runs them again."""
        with transaction(self._conn):
            self._conn.executemany(
                "UPDATE replay_cells SET state = ?, error = ?, allowed = attempts, finished_at = ? WHERE id = ?",
                [(FAILED, error, to_utc_iso(at), i) for i in cell_ids])

    def refund(self, cell_ids: Sequence[int], most: int) -> None:
        """Take back the attempt each cell's last failure used (an outage, not the cell), and
        queue it again, keeping its error; a cell given back `most` already keeps the failure."""
        with transaction(self._conn):
            self._conn.executemany(
                "UPDATE replay_cells SET attempts = MAX(attempts - 1, 0), state = ?, refunds = refunds + 1 "
                "WHERE id = ? AND refunds < ?", [(PENDING, i, most) for i in cell_ids])

    def back_to_pending(self, cell_id: int) -> None:
        """Cut off before it could finish: no attempt counted, its place kept."""
        self._set(cell_id, "state = ?, job_id = NULL, started_at = NULL", PENDING)

    def retry_failed(self, study_id: int, cell_id: int | None = None) -> int:
        """Failed cells (or the one) back to pending at the end of the queue, allowed one more
        attempt than they have used and their outage refunds again. The count moved."""
        with transaction(self._conn):
            end = self._conn.execute("SELECT COALESCE(MAX(position), 0) FROM replay_cells WHERE study_id = ?",
                                     (study_id,)).fetchone()[0]
            ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM replay_cells WHERE study_id = ? AND state = ? AND (? IS NULL OR id = ?) "
                "ORDER BY position, id", (study_id, FAILED, cell_id, cell_id))]
            self._conn.executemany(
                "UPDATE replay_cells SET state = ?, allowed = attempts + 1, refunds = 0, position = ? WHERE id = ?",
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
