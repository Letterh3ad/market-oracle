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


@dataclass(frozen=True)
class StoredTilt:
    account: str
    day: date
    tilt: dict[str, float]
    """method -> the model's multiplier, validated and capped; empty for no tilt."""
    rules: dict[str, float]
    """The rules weights the model was shown, before its tilt."""
    reason: str | None
    error: str | None
    """Why there is no tilt (bad JSON, timeout, the model down); None when it answered."""
    model: str | None
    at: str


class TiltRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, t: StoredTilt) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO regime_tilts (account, day, tilt, rules, reason, error, model, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (t.account, t.day.isoformat(), json.dumps(t.tilt), json.dumps(t.rules), t.reason, t.error, t.model,
                 t.at))

    def get(self, account: str, day: date) -> StoredTilt | None:
        row = self._conn.execute(f"{_TILTS} WHERE account = ? AND day = ?", (account, day.isoformat())).fetchone()
        return _tilt(row) if row else None

    def all(self, account: str) -> dict[date, StoredTilt]:
        rows = self._conn.execute(f"{_TILTS} WHERE account = ? ORDER BY day", (account,)).fetchall()
        return {t.day: t for t in map(_tilt, rows)}


_TILTS = "SELECT account, day, tilt, rules, reason, error, model, at FROM regime_tilts"


def _tilt(row) -> StoredTilt:
    return StoredTilt(row[0], date.fromisoformat(row[1]), json.loads(row[2]), json.loads(row[3]), *row[4:])
