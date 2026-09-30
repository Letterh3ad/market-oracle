from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from oracle.research.news import Article
from oracle.research.notes import Note
from oracle.store.db import to_utc_iso, transaction
from oracle.store.discoveries import DiscoveryEvidence

# Unusable notes by one model after which it is not asked again.
NOTE_ATTEMPTS = 2


@dataclass(frozen=True)
class Reading:
    """What earlier reading left for one article and one model."""

    site: str | None
    text: str | None
    settled: bool
    """Noted by the model, unusable from it NOTE_ATTEMPTS times, or unreadable for good."""


class ArticleRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, articles: list[Article], source: str, fetched_at: str, query: str | None = None) -> list[int]:
        """Ids in the order given. A URL already stored keeps its first row."""
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR IGNORE INTO articles (url, title, outlet, published_at, source, fetched_at, query, body, "
                "site) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(a.url, a.title, a.outlet, to_utc_iso(a.published) if a.published else None, source, fetched_at,
                  query, a.body, a.site) for a in articles],
            )
        return [self._conn.execute("SELECT id FROM articles WHERE url = ?", (a.url,)).fetchone()[0] for a in articles]

    def filings(self, symbols: list[str], since: str) -> list[int]:
        """Stored SEC filings of any of `symbols` filed at or after `since`, newest first."""
        if not symbols:
            return []
        rows = self._conn.execute(
            f"SELECT id FROM articles WHERE source = 'sec_edgar' AND query COLLATE NOCASE IN ({','.join('?' * len(symbols))}) "
            "AND published_at >= ? ORDER BY published_at DESC", (*symbols, since))
        return [r[0] for r in rows]

    def latest_about(self, symbols: list[str], limit: int) -> list[DiscoveryEvidence]:
        """The newest articles research on any of `symbols` found by its own searches (not a
        linked company's), and their stored filings; undated ones by when they were fetched."""
        marks = ",".join("?" * len(symbols))
        rows = self._conn.execute(
            "SELECT a.id, a.title, a.url, a.outlet, a.published_at FROM articles a WHERE a.id IN ("
            "SELECT e.article_id FROM research_evidence e JOIN research_runs r ON r.id = e.run_id "
            f"WHERE r.symbol COLLATE NOCASE IN ({marks}) AND NOT EXISTS (SELECT 1 FROM research_queries q "
            "WHERE q.run_id = e.run_id AND q.query = e.query COLLATE NOCASE AND q.via IS NOT NULL)) "
            f"OR (a.source = 'sec_edgar' AND a.query COLLATE NOCASE IN ({marks})) "
            "ORDER BY COALESCE(a.published_at, a.fetched_at) DESC, a.id DESC LIMIT ?", (*symbols, *symbols, limit))
        return [DiscoveryEvidence(*r) for r in rows]

    def reading(self, article_ids: list[int], model: str) -> dict[int, Reading]:
        rows = self._conn.execute(
            "SELECT a.id, a.site, t.text, t.failed IS NOT NULL AND t.passing = 0, "
            "n.article_id IS NOT NULL AND (n.failed IS NULL OR n.attempts >= ?) FROM articles a "
            "LEFT JOIN article_texts t ON t.article_id = a.id "
            "LEFT JOIN article_notes n ON n.article_id = a.id AND n.model = ? "
            f"WHERE a.id IN ({','.join('?' * len(article_ids))})", (NOTE_ATTEMPTS, model, *article_ids),
        )
        return {i: Reading(site, text, bool(unreadable or noted)) for i, site, text, unreadable, noted in rows}

    def save_text(self, article_id: int, url: str, text: str, at: str) -> None:
        with transaction(self._conn):
            self._conn.execute("INSERT OR REPLACE INTO article_texts (article_id, read_at, url, text) VALUES (?, ?, ?, ?)",
                               (article_id, at, url, text))

    def save_read_failure(self, article_id: int, why: str, passing: bool, at: str) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO article_texts (article_id, read_at, failed, passing) VALUES (?, ?, ?, ?)",
                (article_id, at, why, int(passing)))

    def save_note(self, article_id: int, model: str, note: Note | None, failed: str | None, at: str,
                  attempt: bool = True) -> None:
        """A note, or why there is none (tried again by the next run). `attempt` False: the
        failure was the backend's, not the model's reading, so it never counts toward giving up."""
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO article_notes (article_id, model, noted_at, about, facts, failed, attempts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (article_id, model) DO UPDATE SET "
                "noted_at = excluded.noted_at, about = excluded.about, facts = excluded.facts, "
                "failed = excluded.failed, attempts = attempts + excluded.attempts",
                (article_id, model, at, int(note.about) if note else None,
                 json.dumps(list(note.facts)) if note else None, failed, int(bool(failed) and attempt)))
