from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from oracle.store.db import transaction


@dataclass(frozen=True)
class DailyRow:
    symbol: str
    day: str
    relation: str
    articles: int
    score: float
    events: dict[str, int]
    event_scores: dict[str, float]


class IndustryNewsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def fetched_at(self, symbol: str) -> str | None:
        row = self._conn.execute("SELECT fetched_at FROM industry_fetches WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else None

    def fetched(self, symbols: list[str]) -> dict[str, str]:
        """Casefolded symbol -> when last searched, for those searched before."""
        out: dict[str, str] = {}
        for i in range(0, len(symbols), 500):
            part = symbols[i:i + 500]
            out |= {s.casefold(): at for s, at in self._conn.execute(
                f"SELECT symbol, fetched_at FROM industry_fetches WHERE symbol IN ({','.join('?' * len(part))})", part)}
        return out

    def record_fetch(self, symbol: str, article_ids: Iterable[int], found: int, at: str) -> None:
        """The search and what it kept, in one transaction."""
        ids = list(dict.fromkeys(article_ids))
        with transaction(self._conn):
            self._conn.executemany("INSERT OR IGNORE INTO industry_news (symbol, article_id, found_at) VALUES (?, ?, ?)",
                                   [(symbol, i, at) for i in ids])
            self._conn.execute(
                "INSERT INTO industry_fetches (symbol, fetched_at, found, kept) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (symbol) DO UPDATE SET fetched_at = excluded.fetched_at, found = excluded.found, "
                "kept = excluded.kept", (symbol, at, found, len(ids)))

    def recent(self, symbol: str, since: str) -> list[int]:
        """Articles the company's own searches found, known (published, else fetched) at or after `since`."""
        rows = self._conn.execute(
            "SELECT a.id FROM industry_news n JOIN articles a ON a.id = n.article_id WHERE n.symbol = ? "
            "AND COALESCE(a.published_at, a.fetched_at) >= ? ORDER BY a.id", (symbol, since))
        return [r[0] for r in rows]

    def known_since(self, article_ids: list[int], since: str) -> list[int]:
        out: list[int] = []
        for i in range(0, len(article_ids), 500):
            part = article_ids[i:i + 500]
            out += [r[0] for r in self._conn.execute(
                f"SELECT id FROM articles WHERE id IN ({','.join('?' * len(part))}) "
                "AND COALESCE(published_at, fetched_at) >= ?", (*part, since))]
        return sorted(out)

    def set_subjects(self, symbol: str, pairs: Iterable[tuple[int, str]]) -> None:
        """Which company's search brought each article scored for `symbol`; the first one kept."""
        with transaction(self._conn):
            self._conn.executemany("INSERT OR IGNORE INTO industry_pairs (symbol, article_id, subject) VALUES (?, ?, ?)",
                                   [(symbol, i, s) for i, s in pairs])

    def subjects(self, symbol: str) -> dict[int, str]:
        return dict(self._conn.execute("SELECT article_id, subject FROM industry_pairs WHERE symbol = ?", (symbol,)))

    def save_daily(self, symbol: str, model: str, start: str, end: str, rows: list[DailyRow], at: str) -> None:
        """Replaces the stored days from `start` to `end` (inclusive) with `rows`: a day that lost
        every article (none now) is dropped rather than kept stale."""
        with transaction(self._conn):
            self._conn.execute("DELETE FROM laya_daily WHERE symbol = ? AND model = ? AND day BETWEEN ? AND ?",
                               (symbol, model, start, end))
            self._conn.executemany(
                "INSERT INTO laya_daily (symbol, model, day, relation, articles, score, events, event_scores, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(symbol, model, r.day, r.relation, r.articles, r.score, json.dumps(r.events),
                  json.dumps(r.event_scores), at)
                 for r in rows])

    def daily(self, symbol: str, model: str, start: str, end: str) -> list[DailyRow]:
        rows = self._conn.execute(
            "SELECT symbol, day, relation, articles, score, events, event_scores FROM laya_daily WHERE symbol = ? "
            "AND model = ? "
            "AND day BETWEEN ? AND ? ORDER BY day, relation", (symbol, model, start, end))
        return [DailyRow(*r[:5], json.loads(r[5]), json.loads(r[6])) for r in rows]
