from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from oracle.store.db import transaction


@dataclass(frozen=True)
class TagSummary:
    tag: str
    symbols: tuple[str, ...]
    """Primary symbol of each tagged company."""
    orphaned: tuple[str, ...]
    """Tagged symbols no longer in the universe."""


class TagRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def of_company(self, company_key: str) -> list[str]:
        rows = self._conn.execute(
            """SELECT DISTINCT t.tag FROM tags t JOIN listings x ON x.symbol = t.symbol
               WHERE x.company_key = ? ORDER BY t.tag""",
            (company_key,),
        )
        return [r[0] for r in rows]

    def of_companies(self, company_keys: list[str]) -> dict[str, tuple[str, ...]]:
        if not company_keys:
            return {}
        marks = ", ".join("?" for _ in company_keys)
        out: dict[str, set[str]] = {}
        for key, tag in self._conn.execute(
            f"""SELECT x.company_key, t.tag FROM tags t JOIN listings x ON x.symbol = t.symbol
                WHERE x.company_key IN ({marks})""",
            company_keys,
        ):
            out.setdefault(key, set()).add(tag)
        return {k: tuple(sorted(v)) for k, v in out.items()}

    def add(self, symbol: str, tag: str, created_at: str) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR IGNORE INTO tags (symbol, tag, created_at) VALUES (?, ?, ?)",
                (symbol, tag, created_at),
            )

    def remove(self, symbol: str, tag: str) -> bool:
        """Untag the company `symbol` belongs to, plus `symbol` itself if orphaned."""
        with transaction(self._conn):
            cur = self._conn.execute(
                """DELETE FROM tags WHERE tag = :tag AND (
                       symbol = :symbol COLLATE NOCASE
                       OR symbol IN (
                           SELECT x.symbol FROM listings x WHERE x.company_key = (
                               SELECT company_key FROM listings WHERE symbol = :symbol COLLATE NOCASE
                           )))""",
                {"tag": tag, "symbol": symbol},
            )
            return cur.rowcount > 0

    def all_tags(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT DISTINCT tag FROM tags ORDER BY tag")]

    def summaries(self) -> list[TagSummary]:
        rows = self._conn.execute(
            """SELECT t.tag, t.symbol, p.symbol AS primary_symbol FROM tags t
               LEFT JOIN listings x ON x.symbol = t.symbol
               LEFT JOIN listings p ON p.company_key = x.company_key AND p.is_primary
               ORDER BY t.tag"""
        )
        grouped: dict[str, tuple[set[str], set[str]]] = {}
        for tag, symbol, primary in rows:
            symbols, orphaned = grouped.setdefault(tag, (set(), set()))
            if primary is None:
                orphaned.add(symbol)
            else:
                symbols.add(primary)
        return [
            TagSummary(tag, tuple(sorted(s)), tuple(sorted(o))) for tag, (s, o) in grouped.items()
        ]
