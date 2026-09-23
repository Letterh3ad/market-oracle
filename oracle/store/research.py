from __future__ import annotations

import sqlite3

from oracle.research.trace import ResearchRun, RunStatus, Step
from oracle.store.db import transaction

_RUN = "id, symbol, backend, model, started_at, finished_at, status, findings, error"


class ResearchRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def start(self, symbol: str, backend: str, model: str, started_at: str) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO research_runs (symbol, backend, model, started_at, status) VALUES (?, ?, ?, ?, ?)",
                (symbol, backend, model, started_at, RunStatus.RUNNING),
            )
        return cur.lastrowid

    def add_step(self, run_id: int, step: Step) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO research_steps (run_id, n, thought, action, input, observation) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, step.n, step.thought, step.action, step.input, step.observation),
            )

    def finish(self, run_id: int, status: RunStatus, findings: str | None, error: str | None,
               finished_at: str) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE research_runs SET status = ?, findings = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, findings, error, finished_at, run_id),
            )

    def get(self, run_id: int) -> ResearchRun | None:
        row = self._conn.execute(f"SELECT {_RUN} FROM research_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def latest(self, symbol: str) -> ResearchRun | None:
        row = self._conn.execute(
            f"SELECT {_RUN} FROM research_runs WHERE symbol = ? ORDER BY started_at DESC, id DESC LIMIT 1", (symbol,),
        ).fetchone()
        return self._run(row) if row else None

    def _run(self, row) -> ResearchRun:
        steps = self._conn.execute(
            "SELECT n, thought, action, input, observation FROM research_steps WHERE run_id = ? ORDER BY n", (row[0],),
        )
        return ResearchRun(*row[:6], RunStatus(row[6]), row[7], row[8], tuple(Step(*s) for s in steps))
