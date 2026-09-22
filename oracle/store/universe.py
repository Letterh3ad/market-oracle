from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, fields

from oracle.store.db import transaction
from oracle.universe.listings import MARKET_CAP_BANDS, Listing

_COLUMNS = [f.name for f in fields(Listing)]

_CAP_ORDER = "CASE l.market_cap {} ELSE {} END".format(
    " ".join(f"WHEN '{band}' THEN {i}" for i, band in enumerate(MARKET_CAP_BANDS)),
    len(MARKET_CAP_BANDS),
)
# Symbol hits outweigh name hits.
_SCORE = "bm25(listings_fts, 5.0, 1.0)"


@dataclass(frozen=True)
class SearchHit:
    symbol: str
    name: str
    type: str
    country: str | None
    exchange: str | None
    currency: str | None
    sector: str | None
    industry: str | None
    market_cap: str | None
    delisted: bool
    listings: int
    """How many listings the company has (counting delisted ones only when shown)."""


@dataclass(frozen=True)
class UniverseCounts:
    listings: int
    companies: int
    delisted: int


class UniverseRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_all(self, listings: Iterable[Listing]) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with transaction(self._conn):
            self._conn.execute("DELETE FROM listings")
            self._conn.executemany(
                f"INSERT INTO listings ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                ([getattr(x, c) for c in _COLUMNS] for x in listings),
            )
            self._conn.execute("INSERT INTO listings_fts (listings_fts) VALUES ('rebuild')")

    def counts(self) -> UniverseCounts:
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT company_key), COALESCE(SUM(delisted), 0) FROM listings"
        ).fetchone()
        return UniverseCounts(*row)

    def search(
        self, match: str, query: str, *, all_listings: bool, include_delisted: bool, limit: int
    ) -> list[SearchHit]:
        """`match` is a safe FTS5 expression; `query` the raw text, for exact symbols."""
        params = {"match": match, "q": query.strip(), "delisted": include_delisted, "limit": limit}
        count = (
            "(SELECT COUNT(*) FROM listings x WHERE x.company_key = l.company_key"
            " AND (:delisted OR x.delisted = 0))"
        )
        # Every hit holds every query word, so size outranks text score; bm25 then
        # orders within a band. Scored alone and materialised: bm25() only works while
        # FTS drives the query.
        hits = f"""
            WITH hits AS MATERIALIZED (
                SELECT rowid, {_SCORE} AS score FROM listings_fts WHERE listings_fts MATCH :match
            )"""
        if all_listings:
            sql = f"""{hits}
                SELECT l.*, {count} AS n FROM hits h JOIN listings l ON l.rowid = h.rowid
                WHERE :delisted OR l.delisted = 0
                ORDER BY l.symbol = :q COLLATE NOCASE DESC, {_CAP_ORDER}, h.score,
                         l.company_key, l.is_primary DESC, l.symbol
                LIMIT :limit"""
        else:
            # A company is shown by its primary listing, unless one of its lines is the
            # exact symbol asked for.
            sql = f"""{hits}, companies AS (
                    SELECT l.company_key, MIN(h.score) AS score,
                           MAX(CASE WHEN l.symbol = :q COLLATE NOCASE THEN l.symbol END) AS exact
                    FROM hits h JOIN listings l ON l.rowid = h.rowid
                    WHERE :delisted OR l.delisted = 0
                    GROUP BY l.company_key
                )
                SELECT l.*, {count} AS n FROM companies c
                JOIN listings l ON l.company_key = c.company_key
                 AND l.symbol = COALESCE(c.exact, (
                     SELECT p.symbol FROM listings p
                     WHERE p.company_key = c.company_key AND p.is_primary))
                ORDER BY c.exact IS NOT NULL DESC, {_CAP_ORDER}, c.score, l.name
                LIMIT :limit"""
        return [
            SearchHit(
                symbol=r["symbol"], name=r["name"] or r["symbol"], type=r["type"],
                country=r["country"], exchange=r["exchange"], currency=r["currency"],
                sector=r["sector"], industry=r["industry"], market_cap=r["market_cap"],
                delisted=bool(r["delisted"]), listings=r["n"],
            )
            for r in self._conn.execute(sql, params)
        ]
