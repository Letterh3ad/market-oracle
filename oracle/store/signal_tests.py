"""Kept signal test results, in the research database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from oracle.store.db import transaction

KEEP = 20
"""Results kept per signal; older ones are dropped."""


@dataclass(frozen=True)
class SignalTestRow:
    id: int
    signal: str
    verdict: str
    result: str
    run_at: str


class SignalTestRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, signal: str, verdict: str, result: str, at: str) -> int:
        with transaction(self._conn):
            new = self._conn.execute("INSERT INTO signal_tests (signal, verdict, result, run_at) VALUES (?, ?, ?, ?)",
                                     (signal, verdict, result, at)).lastrowid
            self._conn.execute("DELETE FROM signal_tests WHERE signal = ? AND id NOT IN (SELECT id FROM signal_tests "
                               "WHERE signal = ? ORDER BY id DESC LIMIT ?)", (signal, signal, KEEP))
        return new

    def latest(self) -> list[SignalTestRow]:
        """The newest result of each signal."""
        return [SignalTestRow(*r) for r in self._conn.execute(
            "SELECT id, signal, verdict, result, run_at FROM signal_tests WHERE id IN "
            "(SELECT MAX(id) FROM signal_tests GROUP BY signal) ORDER BY signal")]
