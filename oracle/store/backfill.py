from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from oracle.store.db import to_utc_iso, transaction
from oracle.universe.indexes import Member
from oracle.universe.listings import Listing

ADDED = "added"
CORRECTED = "corrected"
# Grouping sets these on every rebuild, so a stored line keeps neither.
_REGROUPED = ("company_key", "is_primary")


@dataclass(frozen=True)
class IndexPage:
    tried_at: datetime
    fetched_at: datetime | None
    """When the member list was last read; None if it never has been."""
    error: str | None
    """Why the last try failed, when it did."""


@dataclass(frozen=True)
class BackfillRow:
    symbol: str
    kind: str
    """ADDED or CORRECTED."""
    index_code: str
    note: str
    fields: dict
    """The whole line when added; the fields laid over it when corrected."""


@dataclass(frozen=True)
class Try:
    index_code: str
    outcome: str
    failed: bool
    tried_at: datetime


class BackfillRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def page(self, index_code: str) -> IndexPage | None:
        row = self._conn.execute("SELECT tried_at, fetched_at, error FROM index_pages WHERE index_code = ?",
                                 (index_code,)).fetchone()
        return IndexPage(_time(row[0]), _time(row[1]), row[2]) if row else None

    def members(self, index_code: str) -> list[Member]:
        return [Member(r[0], r[1], r[2]) for r in self._conn.execute(
            "SELECT symbol, name, isin FROM index_members WHERE index_code = ? ORDER BY position", (index_code,))]

    def replace_members(self, index_code: str, members: Sequence[Member], at: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM index_members WHERE index_code = ?", (index_code,))
            self._conn.executemany(
                "INSERT INTO index_members (index_code, position, symbol, name, isin) VALUES (?, ?, ?, ?, ?)",
                [(index_code, i, m.symbol, m.name, m.isin) for i, m in enumerate(members)])
            self._save_page(index_code, at, at, None)

    def note_page_error(self, index_code: str, error: str, at: datetime) -> None:
        with transaction(self._conn):
            page = self.page(index_code)
            self._save_page(index_code, at, page.fetched_at if page else None, error)

    def _save_page(self, index_code: str, tried: datetime, fetched: datetime | None, error: str | None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO index_pages (index_code, tried_at, fetched_at, error) VALUES (?, ?, ?, ?)",
            (index_code, to_utc_iso(tried), fetched and to_utc_iso(fetched), error))

    def add(self, line: Listing, index_code: str, note: str, at: datetime) -> None:
        fields = {k: v for k, v in asdict(line).items() if k not in _REGROUPED}
        self._put(line.symbol, ADDED, index_code, note, fields, at)

    def correct(self, symbol: str, fields: dict, index_code: str, note: str, at: datetime) -> None:
        """Lay `fields` over the line on every rebuild, on top of any earlier correction of it."""
        earlier = next((r.fields for r in self.rows() if r.symbol.casefold() == symbol.casefold()
                        and r.kind == CORRECTED), {})
        self._put(symbol, CORRECTED, index_code, note, {**earlier, **fields}, at)

    def _put(self, symbol: str, kind: str, index_code: str, note: str, fields: dict, at: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO backfill_rows (symbol, kind, index_code, note, data, at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (symbol, kind, index_code, note, json.dumps(fields), to_utc_iso(at)))

    def drop(self, symbol: str, kind: str) -> bool:
        with transaction(self._conn):
            return self._conn.execute("DELETE FROM backfill_rows WHERE symbol = ? COLLATE NOCASE AND kind = ?",
                                      (symbol, kind)).rowcount > 0

    def rows(self) -> list[BackfillRow]:
        return [BackfillRow(r[0], r[1], r[2], r[3], json.loads(r[4])) for r in self._conn.execute(
            "SELECT symbol, kind, index_code, note, data FROM backfill_rows ORDER BY at, symbol")]

    def added(self) -> list[Listing]:
        return [Listing(**r.fields) for r in self.rows() if r.kind == ADDED]

    def corrections(self) -> dict[str, dict]:
        return {r.symbol: r.fields for r in self.rows() if r.kind == CORRECTED}

    def note_try(self, symbol: str, index_code: str, outcome: str, failed: bool, at: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO backfill_tries (symbol, index_code, outcome, failed, tried_at) "
                "VALUES (?, ?, ?, ?, ?)", (symbol, index_code, outcome, int(failed), to_utc_iso(at)))

    def tries(self) -> dict[str, Try]:
        """Casefolded symbol -> its last try."""
        return {r[0].casefold(): Try(r[1], r[2], bool(r[3]), _time(r[4])) for r in self._conn.execute(
            "SELECT symbol, index_code, outcome, failed, tried_at FROM backfill_tries")}


def _time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None
