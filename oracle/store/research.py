from __future__ import annotations

import sqlite3
from datetime import date, datetime

from oracle.research.trace import Evidence, ResearchRun, RunStatus, Step
from oracle.store.db import transaction

_RUN = "id, symbol, backend, model, started_at, finished_at, status, findings, error, as_of"


class ResearchRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def start(self, symbol: str, backend: str, model: str, started_at: str, as_of: date | None = None) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO research_runs (symbol, backend, model, started_at, status, as_of) VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, backend, model, started_at, RunStatus.RUNNING, as_of.isoformat() if as_of else None),
            )
        return cur.lastrowid

    def add_step(self, run_id: int, step: Step) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO research_steps (run_id, n, thought, action, input, observation) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, step.n, step.thought, step.action, step.input, step.observation),
            )

    def set_queries(self, run_id: int, queries: list[str]) -> None:
        with transaction(self._conn):
            self._conn.executemany("INSERT INTO research_queries (run_id, n, query) VALUES (?, ?, ?)",
                                   [(run_id, n, q) for n, q in enumerate(queries)])

    def add_evidence(self, run_id: int, article_ids: list[int]) -> None:
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR IGNORE INTO research_evidence (run_id, article_id) VALUES (?, ?)",
                [(run_id, a) for a in article_ids],
            )

    def set_cited(self, run_id: int, article_ids: set[int]) -> None:
        with transaction(self._conn):
            self._conn.executemany(
                "UPDATE research_evidence SET cited = 1 WHERE run_id = ? AND article_id = ?",
                [(run_id, a) for a in article_ids],
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
        evidence = self._conn.execute(
            "SELECT a.id, a.url, a.title, a.outlet, a.published_at, e.cited FROM research_evidence e "
            "JOIN articles a ON a.id = e.article_id WHERE e.run_id = ? "
            "ORDER BY a.published_at IS NULL, a.published_at DESC, a.id", (row[0],),
        )
        return ResearchRun(*row[:6], RunStatus(row[6]), row[7], row[8], tuple(Step(*s) for s in steps),
                           tuple(Evidence(i, u, t, o, datetime.fromisoformat(p) if p else None, bool(c))
                                 for i, u, t, o, p, c in evidence),
                           date.fromisoformat(row[9]) if row[9] else None,
                           tuple(q for (q,) in self._conn.execute(
                               "SELECT query FROM research_queries WHERE run_id = ? ORDER BY n", (row[0],))))
