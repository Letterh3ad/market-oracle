from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from collections.abc import Set
from datetime import date, datetime

from oracle.research.trace import (
    NOT_ABOUT, NOTHING, Claim, Drop, Evidence, Report, ResearchRun, RoleCheck, RunStatus, Step, Verdict,
)
from oracle.store.db import transaction

_RUN = "id, symbol, backend, model, started_at, finished_at, status, findings, error, as_of, fell_back_to"


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

    def save_report(self, run_id: int, symbol: str, report: Report) -> None:
        with transaction(self._conn):
            self._conn.execute("INSERT OR REPLACE INTO research_reports (run_id, symbol, day, body) VALUES (?, ?, ?, ?)",
                               (run_id, symbol, report.day.isoformat(), json.dumps(_report_json(report))))

    def with_reports(self, symbol: str) -> list[ResearchRun]:
        """Runs that produced a report, newest run first (a replay's report day is its as-of)."""
        rows = self._conn.execute(
            f"SELECT {', '.join('r.' + c.strip() for c in _RUN.split(','))} FROM research_runs r "
            "JOIN research_reports p ON p.run_id = r.id WHERE r.symbol = ? ORDER BY r.started_at DESC, r.id DESC", (symbol,),
        ).fetchall()
        return [self._run(row) for row in rows]

    def latest_report(self, symbol: str) -> ResearchRun | None:
        """The newest live run that wrote a report, with a verdict or not."""
        row = self._conn.execute(
            "SELECT r.id FROM research_runs r JOIN research_reports p ON p.run_id = r.id "
            "WHERE r.symbol = ? AND r.as_of IS NULL ORDER BY r.started_at DESC, r.id DESC LIMIT 1", (symbol,),
        ).fetchone()
        return self.get(row[0]) if row else None

    def last_attempt(self, symbol: str) -> datetime | None:
        """When the newest live run started, whatever its outcome."""
        (at,) = self._conn.execute("SELECT MAX(started_at) FROM research_runs WHERE symbol = ? AND as_of IS NULL",
                                   (symbol,)).fetchone()
        return datetime.fromisoformat(at) if at else None

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
               finished_at: str, fell_back_to: Set[str] = frozenset()) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE research_runs SET status = ?, findings = ?, error = ?, finished_at = ?, fell_back_to = ? "
                "WHERE id = ?",
                (status, findings, error, finished_at, json.dumps(sorted(fell_back_to)) if fell_back_to else None,
                 run_id),
            )

    def fail_running(self, error: str, finished_at: str) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE research_runs SET status = ?, error = ?, finished_at = ? WHERE status = ?",
                (RunStatus.FAILED, error, finished_at, RunStatus.RUNNING),
            )

    def set_stage(self, run_id: int, stage: str | None) -> None:
        """What a running run is doing between its steps, for the progress line."""
        with transaction(self._conn):
            self._conn.execute("UPDATE research_runs SET stage = ? WHERE id = ?", (stage, run_id))

    def stage(self, run_id: int) -> str | None:
        row = self._conn.execute("SELECT stage FROM research_runs WHERE id = ?", (run_id,)).fetchone()
        return row[0] if row else None

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
            "SELECT a.id, a.url, a.title, a.outlet, a.published_at, e.cited, a.body, a.query, t.failed, n.about, "
            "n.facts, n.failed FROM research_evidence e JOIN articles a ON a.id = e.article_id "
            "LEFT JOIN article_texts t ON t.article_id = a.id "
            "LEFT JOIN article_notes n ON n.article_id = a.id AND n.model = ? WHERE e.run_id = ? "
            "ORDER BY a.published_at IS NULL, a.published_at DESC, a.id", (note_model(row[2], row[3]), row[0]),
        )
        return ResearchRun(*row[:6], RunStatus(row[6]), row[7], row[8], tuple(Step(*s) for s in steps),
                           tuple(Evidence(i, u, t, o, datetime.fromisoformat(p) if p else None, bool(c), b, q,
                                          *_reading(*r)) for i, u, t, o, p, c, b, q, *r in evidence),
                           date.fromisoformat(row[9]) if row[9] else None,
                           tuple(q for (q,) in self._conn.execute(
                               "SELECT query FROM research_queries WHERE run_id = ? ORDER BY n", (row[0],))),
                           self._report(row[0]), tuple(json.loads(row[10])) if row[10] else ())

    def _report(self, run_id: int) -> Report | None:
        row = self._conn.execute("SELECT day, body FROM research_reports WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        body = json.loads(row[1])

        def claim(c: dict | None) -> Claim | None:
            return Claim(c["text"], tuple(c["cites"]), c["role"]) if c else None

        def verdict(v: dict | None) -> Verdict | None:
            return Verdict(v["label"], v["confidence"], v["reason"], tuple(v["cites"]), v["invalidated_if"]) if v else None

        return Report(date.fromisoformat(row[0]), tuple(claim(c) for c in body["findings"]), claim(body["bull"]),
                      claim(body["bear"]), tuple(claim(c) for c in body["risks"]), body["invalidated_if"],
                      tuple(RoleCheck(c["role"], c["usable"], {Drop(k): n for k, n in c["dropped"].items()})
                            for c in body.get("checks", ())),
                      verdict(body.get("verdict")), body.get("verdict_rejected"), tuple(body.get("unread", ())))


def note_model(backend: str, model: str) -> str:
    """How notes are keyed: each model reads an article its own way."""
    return f"{backend}/{model}"


def _reading(read_failed: str | None, about: int | None, facts: str | None,
             note_failed: str | None) -> tuple[tuple[str, ...], str | None]:
    if facts is not None:
        found = tuple(json.loads(facts)) if about else ()
        return found, None if found else (NOTHING if about else NOT_ABOUT)
    return (), note_failed or read_failed


def _report_json(r: Report) -> dict:
    def claim(c: Claim | None) -> dict | None:
        return {"text": c.text, "cites": list(c.cites), "role": c.role} if c else None

    return {"findings": [claim(c) for c in r.findings], "bull": claim(r.bull), "bear": claim(r.bear),
            "risks": [claim(c) for c in r.risks], "invalidated_if": r.invalidated_if,
            # Kept in the body, not a table: they are only ever read with the report.
            "checks": [{"role": c.role, "usable": c.usable, "dropped": c.dropped} for c in r.checks],
            "verdict": asdict(r.verdict) if r.verdict else None, "verdict_rejected": r.verdict_rejected,
            "unread": list(r.unread)}
