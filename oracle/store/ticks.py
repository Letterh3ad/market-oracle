from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime

from oracle.store.db import to_utc_iso, transaction


INTERRUPTED = "interrupted"
TICK = "tick"
MIDDAY = "midday"
"""The midday pass: discovery and research only, stored beside ticks but never counted as one."""
FILL = "fill"
"""A fill pass: prices, FX and fills only, stored the same way."""
KIND_SEP = "/"
# No column for the kind: a midday run's trigger is stored as "midday/<trigger>", a tick's bare.


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
    kind: str = TICK

    @property
    def outcome(self) -> str:
        if self.note and self.note.startswith(INTERRUPTED):
            return "interrupted"
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
        trigger = run.trigger if run.kind == TICK else f"{run.kind}{KIND_SEP}{run.trigger}"
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO tick_runs (trigger, started_at, finished_at, stages, note) VALUES (?, ?, ?, ?, ?)",
                (trigger, to_utc_iso(run.started_at), to_utc_iso(run.finished_at),
                 json.dumps([asdict(s) for s in run.stages]), run.note),
            )
        return int(cur.lastrowid)

    def recent(self, limit: int = 20, kind: str | None = None) -> list[TickRun]:
        """Newest finish first: a tick skipped while a longer one ran never hides it. Every
        kind unless one is named."""
        where, args = _of_kind(kind) if kind else ("1", ())
        rows = self._conn.execute(
            f"SELECT * FROM tick_runs WHERE {where} ORDER BY finished_at DESC, id DESC LIMIT ?", (*args, limit)
        ).fetchall()
        return [_run(r) for r in rows]

    def finished_since(self, at: datetime, kind: str = TICK) -> bool:
        where, args = _of_kind(kind)
        return self._conn.execute(
            f"SELECT 1 FROM tick_runs WHERE finished_at >= ? AND {where} LIMIT 1", (to_utc_iso(at), *args)
        ).fetchone() is not None


def _of_kind(kind: str) -> tuple[str, tuple]:
    if kind == TICK:
        return f"instr(trigger, '{KIND_SEP}') = 0", ()
    return "trigger LIKE ?", (f"{kind}{KIND_SEP}%",)


def _run(r) -> TickRun:
    kind, _, trigger = r["trigger"].rpartition(KIND_SEP)
    return TickRun(
        trigger, datetime.fromisoformat(r["started_at"]), datetime.fromisoformat(r["finished_at"]),
        tuple(Stage(**s) for s in json.loads(r["stages"])), r["note"], r["id"], kind or TICK,
    )
