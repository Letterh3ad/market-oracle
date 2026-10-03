from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from oracle.store.db import to_utc_iso, transaction

PENDING = "pending"


@dataclass(frozen=True)
class PlanChange:
    id: int
    changes: dict[str, tuple[object, object]]
    """Key: (value when asked, value asked for)."""
    made_at: datetime
    due: datetime
    state: str
    """pending, applied, cancelled or refused."""
    settled_at: datetime | None
    note: str


class PlanChangeRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def pending(self) -> PlanChange | None:
        row = self._conn.execute(f"{_SELECT} WHERE state = ?", (PENDING,)).fetchone()
        return _change(row) if row else None

    def history(self, limit: int = 10) -> list[PlanChange]:
        return [_change(r) for r in self._conn.execute(f"{_SELECT} WHERE state != ? ORDER BY id DESC LIMIT ?",
                                                       (PENDING, limit))]

    def put_pending(self, changes: dict[str, tuple[object, object]], made_at: datetime, due: datetime) -> PlanChange:
        """The one pending change, replaced in place when there is one."""
        raw = json.dumps(changes)
        with transaction(self._conn):
            current = self.pending()
            if current is None:
                self._conn.execute("INSERT INTO plan_changes (changes, made_at, due_at, state) VALUES (?, ?, ?, ?)",
                                   (raw, to_utc_iso(made_at), to_utc_iso(due), PENDING))
            else:
                self._conn.execute("UPDATE plan_changes SET changes = ?, made_at = ?, due_at = ? WHERE id = ?",
                                   (raw, to_utc_iso(made_at), to_utc_iso(due), current.id))
            return self.pending()

    def settle(self, change_id: int, state: str, at: datetime, note: str = "") -> PlanChange:
        with transaction(self._conn):
            self._conn.execute("UPDATE plan_changes SET state = ?, settled_at = ?, note = ? WHERE id = ? AND state = ?",
                               (state, to_utc_iso(at), note, change_id, PENDING))
            return _change(self._conn.execute(f"{_SELECT} WHERE id = ?", (change_id,)).fetchone())


_SELECT = "SELECT id, changes, made_at, due_at, state, settled_at, note FROM plan_changes"


def _change(row: tuple) -> PlanChange:
    id_, changes, made_at, due, state, settled_at, note = row
    return PlanChange(id_, {k: (v[0], v[1]) for k, v in json.loads(changes).items()}, datetime.fromisoformat(made_at),
                      datetime.fromisoformat(due), state, datetime.fromisoformat(settled_at) if settled_at else None,
                      note)
