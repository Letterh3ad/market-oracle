from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from oracle.store.db import transaction

KEEP = 20
"""Results kept per run; older ones are dropped."""


@dataclass(frozen=True)
class TestRow:
    id: int
    run: str
    model: str
    verdict: str
    result: str
    run_at: str


class LayaTestRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, run: str, model: str, verdict: str, result: str, at: str) -> int:
        with transaction(self._conn):
            new = self._conn.execute("INSERT INTO laya_tests (run, model, verdict, result, run_at) VALUES (?, ?, ?, ?, ?)",
                                     (run, model, verdict, result, at)).lastrowid
            self._conn.execute("DELETE FROM laya_tests WHERE run = ? AND id NOT IN "
                               "(SELECT id FROM laya_tests WHERE run = ? ORDER BY id DESC LIMIT ?)", (run, run, KEEP))
        return new

    def latest(self) -> list[TestRow]:
        """The newest result of each run."""
        return [TestRow(*r) for r in self._conn.execute(
            "SELECT id, run, model, verdict, result, run_at FROM laya_tests WHERE id IN "
            "(SELECT MAX(id) FROM laya_tests GROUP BY run) ORDER BY run")]

    def latest_for(self, run: str, model: str) -> TestRow | None:
        """The newest result of `run` for `model`."""
        r = self._conn.execute("SELECT id, run, model, verdict, result, run_at FROM laya_tests WHERE run = ? AND model = ? "
                               "ORDER BY id DESC LIMIT 1", (run, model)).fetchone()
        return TestRow(*r) if r else None
