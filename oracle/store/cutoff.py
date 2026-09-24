from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from oracle.store.db import to_utc_iso, transaction


@dataclass(frozen=True)
class AnswerRow:
    event_id: str
    day: date
    question: str
    expected: str
    reply: str
    right: bool


@dataclass(frozen=True)
class ProbeRow:
    provider: str
    model: str
    profile: str
    question_set: str
    claimed: date
    cutoff: date
    probed_at: datetime
    answers: tuple[AnswerRow, ...]


class CutoffRepo:
    """One cutoff probe per model (provider and model id), with its answers."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace(self, p: ProbeRow) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM cutoff_probes WHERE provider = ? AND model = ?", (p.provider, p.model))
            cur = self._conn.execute(
                "INSERT INTO cutoff_probes (provider, model, profile, question_set, claimed, cutoff, probed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (p.provider, p.model, p.profile, p.question_set, p.claimed.isoformat(), p.cutoff.isoformat(),
                 to_utc_iso(p.probed_at)))
            self._conn.executemany(
                "INSERT INTO cutoff_answers (probe_id, n, event_id, day, question, expected, reply, correct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(cur.lastrowid, n, a.event_id, a.day.isoformat(), a.question, a.expected, a.reply, int(a.right))
                 for n, a in enumerate(p.answers, 1)])

    def get(self, provider: str, model: str) -> ProbeRow | None:
        row = self._conn.execute("SELECT * FROM cutoff_probes WHERE provider = ? AND model = ?",
                                 (provider, model)).fetchone()
        if row is None:
            return None
        answers = self._conn.execute("SELECT * FROM cutoff_answers WHERE probe_id = ? ORDER BY n", (row["id"],))
        return ProbeRow(row["provider"], row["model"], row["profile"], row["question_set"],
                        date.fromisoformat(row["claimed"]), date.fromisoformat(row["cutoff"]),
                        datetime.fromisoformat(row["probed_at"]),
                        tuple(AnswerRow(a["event_id"], date.fromisoformat(a["day"]), a["question"], a["expected"],
                                        a["reply"], bool(a["correct"])) for a in answers))
