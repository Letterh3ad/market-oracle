from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from oracle.store.db import transaction

_COLUMNS = ("c.id, c.symbol, c.other, c.kind, c.article_id, c.evidence, c.known_at, c.rule, c.model, c.choice, c.prob, "
            "c.relation, c.state, c.typed_at, c.decided_at, a.title, a.url")


@dataclass(frozen=True)
class NewEdge:
    symbol: str
    other: str
    kind: str
    article_id: int
    evidence: str
    known_at: str
    rule: str
    model: str
    choice: str
    prob: float
    relation: str
    state: str


@dataclass(frozen=True)
class StoredEdge:
    """`other` is `relation` of `symbol`, as `evidence` (from the article) shows."""

    id: int
    symbol: str
    other: str
    kind: str
    article_id: int
    evidence: str
    known_at: str
    rule: str
    model: str
    choice: str
    prob: float
    relation: str
    state: str
    typed_at: str
    decided_at: str | None
    title: str
    url: str


class EdgeRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def unread(self, symbol: str, model: str, article_ids: Sequence[int]) -> list[int]:
        """Those of `article_ids` not yet read for `symbol` by `model`, in the order given."""
        done = {r[0] for r in self._conn.execute(
            "SELECT article_id FROM edge_reads WHERE symbol = ? AND model = ?", (symbol, model))}
        return [a for a in dict.fromkeys(article_ids) if a not in done]

    def save(self, symbol: str, model: str, read: Sequence[int], edges: Sequence[NewEdge], now: str) -> list[int]:
        """The documents read and the ties they showed, together; a tie already kept stays as it is.
        Returns the ids of the ties, in order."""
        with transaction(self._conn):
            found: dict[int, int] = {}
            for e in edges:
                found[e.article_id] = found.get(e.article_id, 0) + 1
            self._conn.executemany("INSERT OR IGNORE INTO edge_reads VALUES (?, ?, ?, ?, ?)",
                                   [(symbol, a, model, now, found.get(a, 0)) for a in read])
            self._conn.executemany(
                "INSERT OR IGNORE INTO link_candidates (symbol, other, kind, article_id, evidence, known_at, rule, model, "
                "choice, prob, relation, state, typed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(e.symbol, e.other, e.kind, e.article_id, e.evidence, e.known_at, e.rule, e.model, e.choice, e.prob,
                  e.relation, e.state, now) for e in edges])
        return [self._conn.execute("SELECT id FROM link_candidates WHERE symbol = ? AND other = ? AND article_id = ? "
                                   "AND model = ?", (e.symbol, e.other, e.article_id, e.model)).fetchone()[0]
                for e in edges]

    def of(self, symbols: Collection[str], states: Collection[str]) -> list[StoredEdge]:
        """Ties with any of `symbols` at either end in one of `states`, newest document first."""
        marks, kinds = ",".join("?" * len(symbols)), ",".join("?" * len(states))
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM link_candidates c JOIN articles a ON a.id = c.article_id "
            f"WHERE (c.symbol IN ({marks}) OR c.other IN ({marks})) AND c.state IN ({kinds}) "
            "ORDER BY c.known_at DESC, c.id", (*symbols, *symbols, *states))
        return [StoredEdge(*r) for r in rows]

    def get(self, edge_id: int) -> StoredEdge | None:
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM link_candidates c JOIN articles a ON a.id = c.article_id "
                                 "WHERE c.id = ?", (edge_id,)).fetchone()
        return StoredEdge(*row) if row else None

    def decide(self, edge_id: int, state: str, now: str) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE link_candidates SET state = ?, decided_at = ? WHERE id = ?", (state, now, edge_id))

    def set_state(self, edge_ids: Sequence[int], state: str) -> None:
        with transaction(self._conn):
            self._conn.executemany("UPDATE link_candidates SET state = ? WHERE id = ?", [(state, i) for i in edge_ids])
