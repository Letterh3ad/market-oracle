from __future__ import annotations

import sqlite3

from oracle.research.news import Article
from oracle.store.db import to_utc_iso, transaction


class ArticleRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, articles: list[Article], source: str, fetched_at: str) -> list[int]:
        """Ids in the order given. A URL already stored keeps its first row."""
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR IGNORE INTO articles (url, title, outlet, published_at, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(a.url, a.title, a.outlet, to_utc_iso(a.published) if a.published else None, source, fetched_at)
                 for a in articles],
            )
        return [self._conn.execute("SELECT id FROM articles WHERE url = ?", (a.url,)).fetchone()[0] for a in articles]
