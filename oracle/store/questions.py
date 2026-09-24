from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from oracle.store.db import transaction
from oracle.store.discoveries import DiscoveryEvidence, Validation

RUNNING, DONE, FAILED = "running", "done", "failed"


@dataclass(frozen=True)
class Question:
    id: int
    text: str
    refines: int | None
    """The question this follow-up narrows."""
    asked_at: str
    auto: bool = False
    """Each answer adds the shortlisted companies that pass validation to the discovered list."""


@dataclass(frozen=True)
class ShortlistItem:
    n: int
    """Rank in its answer, from 1."""
    name: str
    """As the model named it."""
    symbol: str | None
    company_key: str | None
    company: str | None
    """The universe's name for it."""
    why: str
    evidence: tuple[int, ...]
    skipped: str | None = None
    """Why it is not on the shortlist; None when it is."""
    validation: Validation | None = None
    """What an auto question's validation made of it; None when not validated."""
    universe: str | None = None
    """What the universe screen found it by ("universe: Semiconductors & Semiconductor Equipment,
    Netherlands, Mega Cap"); None when only the news named it."""


@dataclass(frozen=True)
class Answer:
    id: int
    question_id: int
    model: str
    started_at: str
    finished_at: str | None
    status: str
    stage: str | None
    error: str | None
    searches: tuple[str, ...]
    notes: tuple[str, ...]
    """What went wrong or was applied along the way, in words."""
    items: tuple[ShortlistItem, ...] = ()

    @property
    def shortlist(self) -> tuple[ShortlistItem, ...]:
        return tuple(i for i in self.items if i.skipped is None)


_ANSWER = "id, question_id, model, started_at, finished_at, status, stage, error, searches, notes"
_QUESTION = "SELECT q.id, q.text, q.refines, q.asked_at, q.auto FROM discovery_questions q"


def _question(row) -> Question:
    return Question(*row[:4], bool(row[4]))


class QuestionRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, text: str, refines: int | None, now: str) -> Question:
        with transaction(self._conn):
            cur = self._conn.execute("INSERT INTO discovery_questions (text, refines, asked_at) VALUES (?, ?, ?)",
                                     (text, refines, now))
        return Question(cur.lastrowid, text, refines, now)

    def get(self, question_id: int) -> Question | None:
        row = self._conn.execute(f"{_QUESTION} WHERE q.id = ?", (question_id,)).fetchone()
        return _question(row) if row else None

    def all(self) -> list[Question]:
        """Newest first."""
        return [_question(r) for r in self._conn.execute(f"{_QUESTION} ORDER BY q.id DESC")]

    def follow_ups(self, question_id: int) -> list[Question]:
        return [_question(r) for r in self._conn.execute(f"{_QUESTION} WHERE q.refines = ? ORDER BY q.id", (question_id,))]

    def set_auto(self, question_id: int, on: bool) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE discovery_questions SET auto = ? WHERE id = ?", (int(on), question_id))

    def auto(self) -> list[Question]:
        """Auto questions, the one answered longest ago (or never) first."""
        rows = self._conn.execute(
            f"{_QUESTION} LEFT JOIN discovery_answers a ON a.question_id = q.id WHERE q.auto GROUP BY q.id "
            "ORDER BY MAX(a.started_at) IS NOT NULL, MAX(a.started_at), q.id")
        return [_question(r) for r in rows]

    def start(self, question_id: int, model: str, now: str) -> int:
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO discovery_answers (question_id, model, started_at, status) VALUES (?, ?, ?, ?)",
                (question_id, model, now, RUNNING))
        return cur.lastrowid

    def set_stage(self, answer_id: int, stage: str | None) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE discovery_answers SET stage = ? WHERE id = ?", (stage, answer_id))

    def set_searches(self, answer_id: int, searches: Sequence[str]) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE discovery_answers SET searches = ? WHERE id = ?",
                               (json.dumps(list(searches)), answer_id))

    def finish(self, answer_id: int, status: str, error: str | None, notes: Sequence[str],
               items: Sequence[ShortlistItem], now: str) -> None:
        """The outcome and the whole shortlist in one step, so no answer is half saved."""
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT INTO discovery_answer_items (answer_id, n, name, symbol, company_key, company, why, evidence, "
                "skipped, validation, universe) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(answer_id, i.n, i.name, i.symbol, i.company_key, i.company, i.why, json.dumps(list(i.evidence)),
                  i.skipped, i.validation.to_json() if i.validation else None, i.universe) for i in items])
            self._conn.execute(
                "UPDATE discovery_answers SET status = ?, error = ?, notes = ?, stage = NULL, finished_at = ? "
                "WHERE id = ?", (status, error, json.dumps(list(notes)), now, answer_id))

    def answer(self, answer_id: int) -> Answer | None:
        row = self._conn.execute(f"SELECT {_ANSWER} FROM discovery_answers WHERE id = ?", (answer_id,)).fetchone()
        return self._answer(row) if row else None

    def answers(self, question_id: int) -> list[Answer]:
        """Newest first."""
        rows = self._conn.execute(f"SELECT {_ANSWER} FROM discovery_answers WHERE question_id = ? ORDER BY id DESC",
                                  (question_id,))
        return [self._answer(r) for r in rows.fetchall()]

    def item(self, answer_id: int, n: int) -> ShortlistItem | None:
        return next((i for i in self._items(answer_id) if i.n == n), None)

    def fail_running(self, error: str, now: str) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE discovery_answers SET status = ?, error = ?, stage = NULL, finished_at = ? "
                               "WHERE status = ?", (FAILED, error, now, RUNNING))

    def evidence(self, article_ids: Sequence[int]) -> dict[int, DiscoveryEvidence]:
        rows = self._conn.execute(
            f"SELECT id, title, url, outlet, published_at FROM articles WHERE id IN ({','.join('?' * len(article_ids))})",
            tuple(article_ids))
        return {r[0]: DiscoveryEvidence(*r) for r in rows}

    def _answer(self, row) -> Answer:
        return Answer(*row[:8], tuple(json.loads(row[8])), tuple(json.loads(row[9])), self._items(row[0]))

    def _items(self, answer_id: int) -> tuple[ShortlistItem, ...]:
        rows = self._conn.execute(
            "SELECT n, name, symbol, company_key, company, why, evidence, skipped, validation, universe "
            "FROM discovery_answer_items WHERE answer_id = ? ORDER BY n", (answer_id,))
        return tuple(ShortlistItem(*r[:6], tuple(json.loads(r[6])), r[7], Validation.from_json(r[8]), r[9])
                     for r in rows)
