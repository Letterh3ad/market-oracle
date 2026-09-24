from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass, fields

from oracle.store.db import transaction
from oracle.universe.listings import MARKET_CAP_BANDS, Listing, pick_primary
from oracle.universe.query import COMPANY_FIELDS, FILTER_FIELDS, UniverseQuery, fts_match

_COLUMNS = [f.name for f in fields(Listing)]
CLASSIFICATION_LEVELS = ("industry", "industry_group", "sector")
"""ListingProfile fields, the most specific first."""

_CAP_ORDER = "CASE p.market_cap {} END".format(
    " ".join(f"WHEN '{band}' THEN {i}" for i, band in enumerate(MARKET_CAP_BANDS))
)
_NAME = "COALESCE(NULLIF(p.name, ''), l.symbol) COLLATE NOCASE"
# Symbol hits outweigh name hits.
_SCORE = "bm25(listings_fts, 5.0, 1.0)"


_RANKED = """
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY company_key ORDER BY exact DESC, is_primary DESC, symbol
        ) AS rn,
        MIN(score) OVER (PARTITION BY company_key) AS best,
        MAX(exact) OVER (PARTITION BY company_key) AS any_exact
    FROM candidates"""
_ALONE = "SELECT *, 1 AS rn, score AS best, exact AS any_exact FROM candidates"


def _require_field(name: str) -> None:
    # Field names are interpolated into SQL, so this is a real check, not an assert.
    if name not in FILTER_FIELDS:
        raise ValueError(f"not a universe field: {name!r}")


def _column(name: str) -> str:
    return f"p.{name}" if name in COMPANY_FIELDS else f"l.{name}"


@dataclass(frozen=True)
class Resolved:
    symbol: str
    company_key: str
    name: str
    """The company's name, from its primary listing."""
    currency: str | None


PEER_FIELDS = ("industry_group", "sector")


@dataclass(frozen=True)
class PeerRow:
    symbol: str
    """The company's primary listing."""
    name: str
    market_cap: str | None
    """FinanceDatabase's size band, e.g. "Large Cap"."""
    currency: str | None


@dataclass(frozen=True)
class ListingProfile:
    """A listing with its company's fields, read from the primary listing."""

    symbol: str
    """The listing asked about."""
    primary_symbol: str
    primary_currency: str | None
    company_key: str
    name: str
    type: str
    country: str | None
    sector: str | None
    industry_group: str | None
    industry: str | None
    category: str | None
    family: str | None
    exchange: str | None
    """Of the listing asked about."""
    listings: int
    """Active listings of the company."""
    website: str | None
    summary: str | None
    market_cap: str | None = None
    """The company's band (MARKET_CAP_BANDS), when FinanceDatabase gives one."""


@dataclass(frozen=True)
class Facet:
    value: str
    companies: int
    label: str | None = None


@dataclass(frozen=True)
class SearchHit:
    symbol: str
    company_key: str
    name: str
    type: str
    country: str | None
    exchange: str | None
    currency: str | None
    sector: str | None
    industry: str | None
    market_cap: str | None
    category_group: str | None
    category: str | None
    family: str | None
    delisted: bool
    listings: int
    """How many listings the company has (counting delisted ones only when shown)."""
    tags: tuple[str, ...] = ()
    dead: bool = False
    """No price source has had data for it lately."""


@dataclass(frozen=True)
class UniverseCounts:
    listings: int
    companies: int
    delisted: int
    by_type: dict[str, tuple[int, int]]


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

    def listing_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    def counts(self) -> UniverseCounts:
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT company_key), COALESCE(SUM(delisted), 0) FROM listings"
        ).fetchone()
        by_type = {
            r[0]: (r[1], r[2]) for r in self._conn.execute(
                "SELECT type, COUNT(*), COUNT(DISTINCT company_key) FROM listings GROUP BY type"
            )
        }
        return UniverseCounts(*row, by_type)

    def profile(self, symbol: str) -> ListingProfile | None:
        row = self._conn.execute(
            """SELECT l.symbol, p.symbol AS primary_symbol, p.currency AS primary_currency, l.company_key,
                      COALESCE(NULLIF(p.name, ''), p.symbol) AS name, p.type, p.country, p.sector,
                      p.industry_group, p.industry, p.category, p.family, l.exchange,
                      (SELECT COUNT(*) FROM listings x WHERE x.company_key = l.company_key AND x.delisted = 0)
                        AS listings,
                      p.website, p.summary, p.market_cap
               FROM listings l JOIN listings p ON p.company_key = l.company_key AND p.is_primary
               WHERE l.symbol = :s COLLATE NOCASE ORDER BY l.symbol = :s DESC LIMIT 1""",
            {"s": symbol.strip()},
        ).fetchone()
        return ListingProfile(**dict(row)) if row else None

    def peers(self, company_key: str, field: str, value: str, limit: int) -> list[PeerRow]:
        """Other active equity companies sharing `field` (sector or industry_group), by
        primary listing: biggest market-cap band first, then the most widely listed."""
        if field not in PEER_FIELDS:
            raise ValueError(f"peers are grouped by {' or '.join(PEER_FIELDS)}, not {field!r}")
        rows = self._conn.execute(
            f"""SELECT p.symbol, COALESCE(NULLIF(p.name, ''), p.symbol) AS name, p.market_cap, p.currency,
                       (SELECT COUNT(*) FROM listings x WHERE x.company_key = p.company_key AND x.delisted = 0) AS n
                FROM listings p
                WHERE p.is_primary AND p.type = 'equity' AND p.delisted = 0
                  AND p.{field} = :v AND p.company_key != :k
                ORDER BY p.market_cap IS NULL, {_CAP_ORDER}, n DESC, name COLLATE NOCASE
                LIMIT :limit""",
            {"v": value, "k": company_key, "limit": limit},
        )
        return [PeerRow(r["symbol"], r["name"], r["market_cap"], r["currency"]) for r in rows]

    def largest_in(self, countries: Sequence[str], sector: str, bands: Sequence[str]) -> list[Resolved]:
        """Active equity companies of `sector` based in `countries` within `bands`, by primary
        listing: biggest band first, then the most widely listed, as peers are ranked."""
        rows = self._conn.execute(
            f"""SELECT p.symbol, p.company_key, COALESCE(NULLIF(p.name, ''), p.symbol) AS name, p.currency,
                       (SELECT COUNT(*) FROM listings x WHERE x.company_key = p.company_key AND x.delisted = 0) AS n
                FROM listings p
                WHERE p.is_primary AND p.type = 'equity' AND p.delisted = 0 AND p.sector = ?
                  AND p.country IN ({", ".join("?" * len(countries))})
                  AND p.market_cap IN ({", ".join("?" * len(bands))})
                ORDER BY {_CAP_ORDER}, n DESC, p.symbol""",
            (sector, *countries, *bands),
        )
        return [Resolved(r["symbol"], r["company_key"], r["name"], r["currency"]) for r in rows]

    def repick_primary(self, company_key: str, dead: Set[str]) -> None:
        """Choose the company's primary listing again, as a refresh would with `dead`."""
        rows = self.company_listings(company_key)
        if not rows:
            return
        primary = pick_primary(rows, dead).symbol
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE listings SET is_primary = (symbol = ?) WHERE company_key = ?", (primary, company_key)
            )

    def company_listings(self, company_key: str) -> list[Listing]:
        """What ranking a company's listings needs, with the currency to show."""
        return [
            Listing(r["symbol"], r["name"], r["type"], r["country"], r["exchange"], delisted=bool(r["delisted"]),
                    currency=r["currency"], company_key=company_key)
            for r in self._conn.execute(
                "SELECT symbol, name, type, country, exchange, delisted, currency FROM listings WHERE company_key = ?",
                (company_key,),
            )
        ]

    def grouping_rows(self) -> list[Listing]:
        """Every listing with what grouping reads, and its stored company and primary mark."""
        return [
            Listing(r["symbol"], r["name"], r["type"], r["country"], r["exchange"], r["isin"],
                    bool(r["delisted"]), company_key=r["company_key"], is_primary=bool(r["is_primary"]))
            for r in self._conn.execute(
                "SELECT symbol, name, type, country, exchange, isin, delisted, company_key, is_primary "
                "FROM listings ORDER BY rowid")
        ]

    def company_symbols(self, company_key: str) -> list[str]:
        rows = self._conn.execute("SELECT symbol FROM listings WHERE company_key = ?", (company_key,))
        return [r[0] for r in rows]

    def resolve(self, symbol: str) -> Resolved | None:
        """A symbol in any case, as stored, with its company; None if not listed."""
        row = self._conn.execute(
            """SELECT l.symbol, l.company_key, COALESCE(NULLIF(p.name, ''), l.symbol) AS name, l.currency
               FROM listings l JOIN listings p ON p.company_key = l.company_key AND p.is_primary
               WHERE l.symbol = :s COLLATE NOCASE ORDER BY l.symbol = :s DESC LIMIT 1""",
            {"s": symbol.strip()},
        ).fetchone()
        return Resolved(*row) if row else None

    def search(self, q: UniverseQuery) -> list[SearchHit]:
        params: dict = {
            "q": q.text.strip(), "delisted": q.include_delisted, "limit": q.limit, "offset": q.offset
        }
        where = ["(:delisted OR l.delisted = 0)"]
        for name, values in q.filters.items():
            _require_field(name)
            keys = []
            for i, value in enumerate(values):
                params[f"{name}_{i}"] = value
                keys.append(f":{name}_{i}")
            where.append(f"{_column(name)} COLLATE NOCASE IN ({', '.join(keys)})")
        for i, tag in enumerate(q.tags):
            params[f"tag_{i}"] = tag
            where.append(
                f"""l.company_key IN (SELECT x.company_key FROM tags t
                    JOIN listings x ON x.symbol = t.symbol WHERE t.tag = :tag_{i})"""
            )

        match = fts_match(q.text)
        # With nothing able to pick a non-primary line, each company is its primary
        # alone: no company join, no per-company ranking. Several times faster.
        primaries_only = not (match or q.all_listings or "exchange" in q.filters)
        if primaries_only:
            where.append("l.is_primary")
        if match:
            params["match"] = match
            # Scored alone and materialised: bm25() only works while FTS drives the query.
            head = f"""WITH hits AS MATERIALIZED (
                SELECT rowid, {_SCORE} AS score FROM listings_fts WHERE listings_fts MATCH :match
            ),"""
            source, score = "hits h JOIN listings l ON l.rowid = h.rowid", "h.score"
        else:
            head, source, score = "WITH", "listings l", "0.0"

        # A company is shown by one line: the exact symbol asked for, else its primary,
        # else (when a filter excludes the primary) its first remaining line.
        sql = f"""{head} candidates AS (
                SELECT l.rowid AS rid, p.rowid AS prid, l.company_key, l.symbol, l.is_primary,
                       {score} AS score, l.symbol = :q COLLATE NOCASE AS exact
                FROM {source}
                JOIN listings p ON {"p.rowid = l.rowid" if primaries_only else
                                     "p.company_key = l.company_key AND p.is_primary"}
                WHERE {" AND ".join(where)}
            ), ranked AS ({_ALONE if primaries_only else _RANKED})
            SELECT l.symbol, l.company_key, l.exchange, l.currency, l.delisted,
                   EXISTS (SELECT 1 FROM listing_health h WHERE h.symbol = l.symbol
                           AND h.dead_since IS NOT NULL) AS dead,
                   p.name, p.type, p.country, p.sector, p.industry, p.market_cap,
                   p.category_group, p.category, p.family,
                   (SELECT COUNT(*) FROM listings x WHERE x.company_key = l.company_key
                     AND (:delisted OR x.delisted = 0)) AS n
            FROM ranked r
            JOIN listings l ON l.rowid = r.rid
            JOIN listings p ON p.rowid = r.prid
            {"" if q.all_listings else "WHERE r.rn = 1"}
            ORDER BY {self._order(q, bool(match))}
            LIMIT :limit OFFSET :offset"""
        return [
            SearchHit(
                symbol=r["symbol"], company_key=r["company_key"], name=r["name"] or r["symbol"], type=r["type"],
                country=r["country"], exchange=r["exchange"], currency=r["currency"],
                sector=r["sector"], industry=r["industry"], market_cap=r["market_cap"],
                category_group=r["category_group"], category=r["category"], family=r["family"],
                delisted=bool(r["delisted"]), listings=r["n"], dead=bool(r["dead"]),
            )
            for r in self._conn.execute(sql, params)
        ]

    @staticmethod
    def _order(q: UniverseQuery, has_text: bool) -> str:
        sort, descending = q.sort or "relevance", q.descending
        if sort == "relevance" and not has_text:
            sort, descending = "market_cap", True
        way = " DESC" if descending else ""
        # Ties inside a company (all-listings mode) put the primary first.
        tail = f"{_NAME}, l.company_key, l.is_primary DESC, l.symbol"
        if sort == "relevance":
            exact, score = ("r.exact", "r.score") if q.all_listings else ("r.any_exact", "r.best")
            # Every hit holds every query word, so size outranks text score.
            return f"{exact} DESC, p.market_cap IS NULL, {_CAP_ORDER}, {score}, {tail}"
        if sort == "market_cap":
            # Bands are ranked biggest first, so ascending (smallest first) reverses the rank.
            return f"p.market_cap IS NULL, {_CAP_ORDER}{'' if descending else ' DESC'}, {tail}"
        if sort == "name":
            return f"{_NAME}{way}, {tail}"
        if sort == "symbol":
            return f"l.symbol COLLATE NOCASE{way}, {tail}"
        column = _column(sort)
        return f"{column} IS NULL, {column} COLLATE NOCASE{way}, {tail}"

    def classifications(self) -> dict[str, frozenset[str]]:
        """Every industry, industry group and sector an active company has, by level
        (`CLASSIFICATION_LEVELS`, the most specific first)."""
        rows = self._conn.execute(
            "SELECT industry, industry_group, sector FROM listings WHERE is_primary AND delisted = 0 "
            "GROUP BY sector, industry_group, industry").fetchall()
        return {level: frozenset(r[n] for r in rows if r[n]) for n, level in enumerate(CLASSIFICATION_LEVELS)}

    def facets(self, name: str) -> list[Facet]:
        """Values of a field with how many active companies have each."""
        _require_field(name)
        if name == "exchange":
            sql = """SELECT l.exchange AS value, COUNT(DISTINCT l.company_key) AS n,
                            MAX(l.market) AS label
                     FROM listings l WHERE l.delisted = 0 AND l.exchange IS NOT NULL
                     GROUP BY l.exchange"""
        else:
            # A company's primary is active whenever any of its lines is.
            sql = f"""SELECT p.{name} AS value, COUNT(*) AS n, NULL AS label
                      FROM listings p
                      WHERE p.is_primary AND p.delisted = 0 AND p.{name} IS NOT NULL
                      GROUP BY p.{name}"""
        rows = [Facet(r["value"], r["n"], r["label"]) for r in self._conn.execute(sql)]
        if name == "market_cap":
            order = {band: i for i, band in enumerate(MARKET_CAP_BANDS)}
            return sorted(rows, key=lambda f: order.get(f.value, len(order)))
        return sorted(rows, key=lambda f: (-f.companies, f.value))

    def values(self, name: str) -> list[str]:
        _require_field(name)
        sql = f"SELECT DISTINCT {name} FROM listings WHERE {name} IS NOT NULL"
        return [r[0] for r in self._conn.execute(sql)]
