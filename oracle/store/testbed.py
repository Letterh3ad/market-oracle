"""The testbed's membership snapshot, its members' sectors and fetch notes, and the coverage join
of member-days against stored bars. Lives in the research database only."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.store.db import transaction


@dataclass(frozen=True)
class Snapshot:
    source: str
    url: str
    revision: str | None
    fetched_at: str
    first_day: date
    last_day: date
    changes: int


@dataclass(frozen=True)
class MemberRow:
    ticker: str
    sector: str | None
    sector_source: str | None
    sector_tried_at: str | None
    bars_tried_at: str | None
    bars_note: str | None
    cik: int | None = None
    identity: str | None = None
    """SAME or OTHER: whether today's holder of a former member's ticker is that member, by SEC; None undecided."""
    identity_note: str | None = None


@dataclass(frozen=True)
class Span:
    ticker: str
    start: date
    end: date | None
    """The first day it is no longer a member; None while it still is."""


@dataclass(frozen=True)
class Alias:
    """An old ticker priced from a renamed company's series under `new` (accepted), or a pair kept for review."""

    old: str
    new: str
    effective_day: date | None
    accepted: bool
    evidence: str
    verified_at: str
    priced_to: date | None = None
    """The last day `new`'s bars stand for `old`: its last member day plus the tail kept for forward returns."""


@dataclass(frozen=True)
class YearTicker:
    year: int
    ticker: str
    member_days: int
    with_bars: int
    with_bars_mapped: int = 0
    """With a bar of its own or, for a renamed ticker, of the ticker it became."""


class TestbedRepo:
    __test__ = False

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, s: Snapshot, rows: Sequence[tuple[date, tuple[str, ...]]]) -> None:
        """Replace the membership with `rows`, and add every ticker in them as a member."""
        with transaction(self._conn):
            self._conn.execute("DELETE FROM tb_membership")
            self._conn.executemany("INSERT INTO tb_membership (day, tickers) VALUES (?, ?)",
                                   [(d.isoformat(), ",".join(t)) for d, t in rows])
            self._conn.execute(
                "INSERT OR REPLACE INTO tb_snapshot (id, source, url, revision, fetched_at, first_day, last_day, "
                "changes) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                (s.source, s.url, s.revision, s.fetched_at, s.first_day.isoformat(), s.last_day.isoformat(), s.changes))
            self._conn.executemany("INSERT OR IGNORE INTO tb_members (ticker) VALUES (?)",
                                   [(t,) for t in {t for _, ts in rows for t in ts}])

    def snapshot(self) -> Snapshot | None:
        r = self._conn.execute("SELECT * FROM tb_snapshot WHERE id = 1").fetchone()
        if r is None:
            return None
        return Snapshot(r["source"], r["url"], r["revision"], r["fetched_at"], date.fromisoformat(r["first_day"]),
                        date.fromisoformat(r["last_day"]), r["changes"])

    def members_on(self, day: date) -> frozenset[str]:
        r = self._conn.execute("SELECT tickers FROM tb_membership WHERE day <= ? ORDER BY day DESC LIMIT 1",
                               (day.isoformat(),)).fetchone()
        return frozenset(r[0].split(",")) if r and r[0] else frozenset()

    def spans(self) -> list[Span]:
        """Every stretch each ticker was a member, oldest first."""
        open_: dict[str, date] = {}
        out: list[Span] = []
        for day, tickers in self._conn.execute("SELECT day, tickers FROM tb_membership ORDER BY day"):
            d, now = date.fromisoformat(day), set(tickers.split(",")) if tickers else set()
            for t in [t for t in open_ if t not in now]:
                out.append(Span(t, open_.pop(t), d))
            for t in now - open_.keys():
                open_[t] = d
        out += [Span(t, s, None) for t, s in open_.items()]
        return sorted(out, key=lambda x: (x.start, x.ticker))

    def members(self) -> list[MemberRow]:
        return [MemberRow(**dict(r)) for r in self._conn.execute(
            "SELECT ticker, sector, sector_source, sector_tried_at, bars_tried_at, bars_note, cik, identity, identity_note "
            "FROM tb_members ORDER BY ticker")]

    def sector(self, ticker: str) -> str | None:
        r = self._conn.execute("SELECT sector FROM tb_members WHERE ticker = ?", (ticker,)).fetchone()
        return r[0] if r else None

    def set_sectors(self, sectors: dict[str, str], source: str, at: str) -> None:
        """Only members already known: a sector source's own list never adds one."""
        with transaction(self._conn):
            self._conn.executemany(
                "UPDATE tb_members SET sector = ?, sector_source = ?, sector_tried_at = ? WHERE ticker = ?",
                [(s, source, at, t) for t, s in sectors.items()])

    def note_sector_try(self, ticker: str, at: str) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE tb_members SET sector_tried_at = ? WHERE ticker = ?", (at, ticker))

    def note_bars(self, ticker: str, at: str, note: str | None) -> None:
        with transaction(self._conn):
            self._conn.execute("INSERT INTO tb_members (ticker, bars_tried_at, bars_note) VALUES (?, ?, ?) "
                               "ON CONFLICT(ticker) DO UPDATE SET bars_tried_at = excluded.bars_tried_at, "
                               "bars_note = excluded.bars_note", (ticker, at, note))

    def set_identity(self, ticker: str, identity: str | None, note: str | None, cik: int | None) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE tb_members SET identity = ?, identity_note = ?, cik = ? WHERE ticker = ?",
                               (identity, note, cik, ticker))

    def set_ciks(self, ciks: dict[str, int | None]) -> None:
        with transaction(self._conn):
            self._conn.executemany("UPDATE tb_members SET cik = ? WHERE ticker = ?", [(c, t) for t, c in ciks.items()])

    def save_aliases(self, aliases: Iterable[Alias]) -> None:
        """Replace every alias with these."""
        with transaction(self._conn):
            self._conn.execute("DELETE FROM tb_aliases")
            self._conn.executemany(
                "INSERT OR REPLACE INTO tb_aliases (old, new, effective_day, accepted, evidence, verified_at, priced_to) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(a.old, a.new, _iso(a.effective_day), int(a.accepted), a.evidence, a.verified_at, _iso(a.priced_to))
                 for a in aliases])

    def aliases(self, accepted: bool | None = None) -> list[Alias]:
        where = "" if accepted is None else f" WHERE accepted = {int(accepted)}"
        return [Alias(r["old"], r["new"], _day(r["effective_day"]), bool(r["accepted"]), r["evidence"],
                      r["verified_at"], _day(r["priced_to"]))
                for r in self._conn.execute(f"SELECT * FROM tb_aliases{where} ORDER BY old, new")]

    def drop_bars(self, ticker: str) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM bars WHERE symbol = ?", (ticker,))

    def year_tickers(self, spans: Iterable[Span], calendar: Sequence[date]) -> list[YearTicker]:
        """Per year and ticker: its trading days as a member (`calendar` days inside its spans), how
        many of those have a stored bar, and how many have one counting accepted aliases."""
        conn = self._conn
        with transaction(conn):
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS tb_cal (day TEXT PRIMARY KEY) WITHOUT ROWID")
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS tb_spans (ticker TEXT, start TEXT, stop TEXT)")
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS tb_alias_map (old TEXT PRIMARY KEY, new TEXT, upto TEXT)")
            conn.execute("DELETE FROM tb_alias_map")
            conn.execute("INSERT INTO tb_alias_map SELECT old, new, COALESCE(priced_to, '9999-12-31') FROM tb_aliases "
                         "WHERE accepted = 1 GROUP BY old")
            conn.execute("DELETE FROM tb_cal")
            conn.execute("DELETE FROM tb_spans")
            conn.executemany("INSERT INTO tb_cal (day) VALUES (?)", [(d.isoformat(),) for d in calendar])
            conn.executemany("INSERT INTO tb_spans VALUES (?, ?, ?)",
                             [(s.ticker, s.start.isoformat(), (s.end or date.max).isoformat()) for s in spans])
            rows = conn.execute(
                "SELECT CAST(substr(c.day, 1, 4) AS INTEGER) AS y, s.ticker, COUNT(*) AS n, COUNT(b.date) AS priced, "
                "COUNT(COALESCE(b.date, m.date)) AS mapped "
                "FROM tb_spans s JOIN tb_cal c ON c.day >= s.start AND c.day < s.stop "
                "LEFT JOIN bars b ON b.symbol = s.ticker AND b.date = c.day "
                "LEFT JOIN tb_alias_map a ON a.old = s.ticker AND c.day <= a.upto "
                "LEFT JOIN bars m ON m.symbol = a.new AND m.date = c.day "
                "GROUP BY y, s.ticker ORDER BY y, s.ticker").fetchall()
        return [YearTicker(r["y"], r["ticker"], r["n"], r["priced"], r["mapped"]) for r in rows]

    def bar_days(self, symbol: str, start: date, end: date) -> list[date]:
        return [date.fromisoformat(d) for (d,) in self._conn.execute(
            "SELECT date FROM bars WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY date",
            (symbol, start.isoformat(), end.isoformat()))]


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _day(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None
