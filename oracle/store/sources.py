from __future__ import annotations

import sqlite3
from dataclasses import dataclass

RECENT_WINDOW_S = 3600.0


@dataclass
class SourceState:
    source: str
    next_allowed: float = 0.0
    run_429: int = 0
    run_5xx: int = 0
    run_403: int = 0
    tripped_at: float | None = None
    reason: str = ""


class SourceStateRepo:
    """Row access only. Callers hold the transaction so read-modify-write is atomic."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load(self, source: str) -> SourceState:
        row = self._conn.execute(
            "SELECT * FROM source_state WHERE source = ?", (source,)
        ).fetchone()
        return SourceState(**dict(row)) if row else SourceState(source=source)

    def save(self, s: SourceState) -> None:
        self._conn.execute(
            "INSERT INTO source_state (source, next_allowed, run_429, run_5xx, run_403, "
            "tripped_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source) DO UPDATE SET next_allowed = excluded.next_allowed, "
            "run_429 = excluded.run_429, run_5xx = excluded.run_5xx, "
            "run_403 = excluded.run_403, tripped_at = excluded.tripped_at, "
            "reason = excluded.reason",
            (s.source, s.next_allowed, s.run_429, s.run_5xx, s.run_403, s.tripped_at, s.reason),
        )

    def log_request(self, source: str, at: float) -> None:
        self._conn.execute("INSERT INTO source_requests (source, at) VALUES (?, ?)", (source, at))
        self._conn.execute(
            "DELETE FROM source_requests WHERE source = ? AND at < ?",
            (source, at - RECENT_WINDOW_S),
        )

    def recent_requests(self, source: str, now: float) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM source_requests WHERE source = ? AND at > ? AND at <= ?",
            (source, now - RECENT_WINDOW_S, now),
        ).fetchone()
        return int(row[0])
