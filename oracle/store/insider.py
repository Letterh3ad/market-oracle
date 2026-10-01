"""Form 4 insider trades from SEC's quarterly data sets and the typed filings (Form 4, 8-K) of
the testbed's issuers. Lives in the research database only."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date

from oracle.research.insider import Filing, Trade
from oracle.store.db import transaction


class InsiderRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_quarter(self, quarter: str, url: str, trades: Sequence[Trade], at: str) -> None:
        """A quarter's trades, replacing what was stored for it (a refetch is whole)."""
        rows = [(t.accession, n, t.issuer_cik, t.owner_cik, t.owner, t.role, t.code, t.traded.isoformat(), t.shares,
                 t.price, t.filed.isoformat(), quarter) for n, t in enumerate(trades)]
        with transaction(self._conn):
            self._conn.execute("DELETE FROM insider_trades WHERE quarter = ?", (quarter,))
            self._conn.executemany("INSERT OR REPLACE INTO insider_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   rows)
            self._conn.execute("INSERT OR REPLACE INTO insider_quarters VALUES (?, ?, ?, ?)",
                               (quarter, url, len(rows), at))

    def quarters(self) -> dict[str, int]:
        """quarter -> trades stored."""
        return dict(self._conn.execute("SELECT quarter, trades FROM insider_quarters ORDER BY quarter"))

    def trades(self, cik: int) -> list[Trade]:
        return [Trade(r[0], r[1], r[2], r[3], r[4], r[5], date.fromisoformat(r[6]), r[7], r[8], date.fromisoformat(r[9]))
                for r in self._conn.execute(
                    "SELECT accession, issuer_cik, owner_cik, owner, role, code, traded, shares, price, filed "
                    "FROM insider_trades WHERE issuer_cik = ? ORDER BY filed, accession, row", (cik,))]

    def replace_filings(self, cik: int, filings: Iterable[Filing], at: str) -> int:
        rows = [(f.accession, cik, f.form, f.items, f.filed.isoformat(), f.accepted) for f in filings]
        with transaction(self._conn):
            self._conn.execute("DELETE FROM sec_filings WHERE cik = ?", (cik,))
            self._conn.executemany("INSERT OR REPLACE INTO sec_filings VALUES (?, ?, ?, ?, ?, ?)", rows)
            self._conn.execute("INSERT OR REPLACE INTO sec_filings_tried VALUES (?, ?, ?)", (cik, at, len(rows)))
        return len(rows)

    def filings_tried(self) -> dict[int, str]:
        return dict(self._conn.execute("SELECT cik, tried_at FROM sec_filings_tried"))

    def filings(self, cik: int) -> list[Filing]:
        return [Filing(r[0], r[1], r[2], r[3], date.fromisoformat(r[4]), r[5]) for r in self._conn.execute(
            "SELECT accession, cik, form, items, filed, accepted FROM sec_filings WHERE cik = ? ORDER BY filed", (cik,))]

    def accepted(self, cik: int) -> dict[str, str]:
        return dict(self._conn.execute(
            "SELECT accession, accepted FROM sec_filings WHERE cik = ? AND accepted IS NOT NULL", (cik,)))

    def counts_by_year(self) -> dict[int, dict[str, int]]:
        """year of filing -> {"P": purchases, "S": sales, "P_issuers": issuers with a purchase}."""
        out: dict[int, dict[str, int]] = {}
        for y, code, n, issuers in self._conn.execute(
                "SELECT substr(filed, 1, 4), code, COUNT(*), COUNT(DISTINCT issuer_cik) FROM insider_trades "
                "GROUP BY 1, 2"):
            out.setdefault(int(y), {"P": 0, "S": 0, "P_issuers": 0, "S_issuers": 0})
            out[int(y)][code] = n
            out[int(y)][f"{code}_issuers"] = issuers
        return out

    def items_by_year(self) -> dict[int, dict[str, int]]:
        """year of filing -> form or 8-K item -> filings."""
        out: dict[int, dict[str, int]] = {}
        for form, items, filed in self._conn.execute("SELECT form, items, filed FROM sec_filings"):
            row = out.setdefault(int(filed[:4]), {})
            keys = [form] + ([f"8-K {i}" for i in items.split(",") if i in ("2.02", "1.01", "2.01")]
                             if form == "8-K" else [])
            for k in keys:
                row[k] = row.get(k, 0) + 1
        return out
