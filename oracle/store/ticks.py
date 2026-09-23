from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime

from oracle.store.db import to_utc_iso, transaction


@dataclass(frozen=True)
class Stage:
    name: str
    state: str
    """ok; partial (it ran, some listings or currencies failed); failed (it could not run)."""
    summary: str
    error: str | None = None
    """The stage's first error, if any."""


@dataclass(frozen=True)
class TickRun:
    trigger: str
    started_at: datetime
    finished_at: datetime
    stages: tuple[Stage, ...]
    note: str | None = None
    id: int | None = None

    @property
    def outcome(self) -> str:
        if not self.stages:
            return "skipped"
        if all(s.state == "failed" for s in self.stages):
            return "failed"
        return "ok" if all(s.state == "ok" for s in self.stages) else "partial"

    @property
    def first_error(self) -> str | None:
        return next((f"{s.name}: {s.error}" for s in self.stages if s.error), None)


class TickRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, run: TickRun) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO tick_runs (trigger, started_at, finished_at, stages, note) VALUES (?, ?, ?, ?, ?)",
                (run.trigger, to_utc_iso(run.started_at), to_utc_iso(run.finished_at),
                 json.dumps([asdict(s) for s in run.stages]), run.note),
            )
        return int(cur.lastrowid)

    def recent(self, limit: int = 20) -> list[TickRun]:
        rows = self._conn.execute(
            "SELECT * FROM tick_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_run(r) for r in rows]


def _run(r) -> TickRun:
    return TickRun(
        r["trigger"], datetime.fromisoformat(r["started_at"]), datetime.fromisoformat(r["finished_at"]),
        tuple(Stage(**s) for s in json.loads(r["stages"])), r["note"], r["id"],
    )
