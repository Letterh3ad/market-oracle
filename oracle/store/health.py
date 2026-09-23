from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from oracle.store.db import transaction


@dataclass(frozen=True)
class ListingHealth:
    symbol: str
    streak: int
    """Distinct days every price source answered "no data", since bars last came."""
    since: date | None
    """When the listing was marked dead: the first day of that streak. None while alive."""

    @property
    def dead(self) -> bool:
        return self.since is not None


class HealthRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, symbol: str) -> ListingHealth:
        row = self._conn.execute(
            "SELECT no_data_days, dead_since FROM listing_health WHERE symbol = ?", (symbol,)
        ).fetchone()
        if row is None:
            return ListingHealth(symbol, 0, None)
        return ListingHealth(symbol, len(json.loads(row[0])), row[1] and date.fromisoformat(row[1]))

    def note_no_data(self, symbol: str, day: date, dead_after: int) -> ListingHealth:
        """Count `day` once, and mark the listing dead at `dead_after` distinct days."""
        with transaction(self._conn):
            row = self._conn.execute(
                "SELECT no_data_days, dead_since FROM listing_health WHERE symbol = ?", (symbol,)
            ).fetchone()
            days = sorted(set(json.loads(row[0]) if row else []) | {day.isoformat()})
            dead = row[1] if row and row[1] else (days[0] if len(days) >= dead_after else None)
            self._conn.execute(
                "INSERT OR REPLACE INTO listing_health (symbol, no_data_days, dead_since) VALUES (?, ?, ?)",
                (symbol, json.dumps(days), dead),
            )
        return self.get(symbol)

    def clear(self, symbol: str) -> bool:
        """Forget the streak; True when the listing had been dead."""
        with transaction(self._conn):
            was_dead = self.get(symbol).dead
            self._conn.execute("DELETE FROM listing_health WHERE symbol = ?", (symbol,))
        return was_dead

    def dead_symbols(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT symbol FROM listing_health WHERE dead_since IS NOT NULL")}
