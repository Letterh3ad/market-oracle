from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from oracle.store.db import transaction


@dataclass(frozen=True)
class StoredState:
    account: str
    day: date
    state: dict
    """The regime reading as plain JSON: mood, flags, readings, preset and final weights,
    warnings, good signs, health and tilt."""
    at: str
    """When the step that read it ran, UTC ISO."""


class RegimeStateRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, account: str, day: date, state: dict, at: str) -> None:
        """One reading per account and market day; a later step that day replaces it."""
        with transaction(self._conn):
            self._conn.execute("INSERT OR REPLACE INTO regime_states (account, day, state, at) VALUES (?, ?, ?, ?)",
                               (account, day.isoformat(), json.dumps(state), at))

    def latest(self, account: str) -> StoredState | None:
        row = self._conn.execute("SELECT account, day, state, at FROM regime_states WHERE account = ? "
                                 "ORDER BY day DESC LIMIT 1", (account,)).fetchone()
        return StoredState(row[0], date.fromisoformat(row[1]), json.loads(row[2]), row[3]) if row else None
