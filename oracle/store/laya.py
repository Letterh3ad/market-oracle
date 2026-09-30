from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from oracle.research.laya import LayaScore
from oracle.store.db import transaction


@dataclass(frozen=True)
class ArticleText:
    id: int
    title: str
    snippet: str | None


@dataclass(frozen=True)
class StoredScore:
    article_id: int
    title: str
    url: str
    outlet: str | None
    published_at: str | None
    fetched_at: str
    relation: str
    relevant: float
    direction: str
    direction_probs: dict[str, float]
    event: str
    event_prob: float
    model: str
    scored_at: str


class LayaRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def company_articles(self, symbols: list[str]) -> list[int]:
        """Articles research on any of a company's `symbols` was shown, and its SEC filings; never
        the stored-data summaries Market Oracle writes itself, nor what only a linked company's
        search showed (that is the linked company's news, scored under another relation)."""
        marks = ",".join("?" * len(symbols))
        rows = self._conn.execute(
            "SELECT e.article_id FROM research_evidence e JOIN research_runs r ON r.id = e.run_id "
            f"JOIN articles a ON a.id = e.article_id WHERE r.symbol COLLATE NOCASE IN ({marks}) "
            "AND a.source != 'oracle' AND NOT EXISTS (SELECT 1 FROM research_queries q WHERE q.run_id = e.run_id "
            "AND q.query = e.query COLLATE NOCASE AND q.via IS NOT NULL) "
            f"UNION SELECT id FROM articles WHERE source = 'sec_edgar' AND query COLLATE NOCASE IN ({marks})",
            (*symbols, *symbols))
        return [r[0] for r in rows]

    def unscored(self, symbol: str, model: str, article_ids: list[int]) -> list[ArticleText]:
        """Those of `article_ids` not yet scored for `symbol` by `model`, oldest id first."""
        out: list[ArticleText] = []
        for i in range(0, len(article_ids), 500):
            part = article_ids[i:i + 500]
            out += [ArticleText(*r) for r in self._conn.execute(
                f"SELECT a.id, a.title, a.snippet FROM articles a WHERE a.id IN ({','.join('?' * len(part))}) "
                "AND NOT EXISTS (SELECT 1 FROM laya_scores s WHERE s.article_id = a.id AND s.symbol = ? "
                "AND s.model = ?)", (*part, symbol, model))]
        return sorted(out, key=lambda a: a.id)

    def save(self, symbol: str, model: str, relation: str, scored: list[tuple[int, LayaScore]], at: str) -> None:
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR IGNORE INTO laya_scores (article_id, symbol, model, relation, relevant, direction, "
                "direction_probs, event, event_prob, scored_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(i, symbol, model, relation, s.relevant, s.direction, json.dumps(s.direction_probs), s.event,
                  s.event_prob, at) for i, s in scored])

    def scores(self, symbol: str, model: str) -> list[StoredScore]:
        rows = self._conn.execute(
            "SELECT a.id, a.title, a.url, a.outlet, a.published_at, a.fetched_at, s.relation, s.relevant, "
            "s.direction, s.direction_probs, s.event, s.event_prob, s.model, s.scored_at FROM laya_scores s "
            "JOIN articles a ON a.id = s.article_id WHERE s.symbol = ? AND s.model = ?", (symbol, model))
        return [StoredScore(*r[:9], json.loads(r[9]), *r[10:]) for r in rows]
