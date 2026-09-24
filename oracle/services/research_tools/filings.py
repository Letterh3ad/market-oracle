"""SEC EDGAR: a US-listed company's recent key filings, dated, readable with read_page, and
the ones the reading stage reads."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from oracle.clock import Clock
from oracle.net.client import RateLimitedClient
from oracle.research import edgar
from oracle.research.news import Article
from oracle.research.tools import ToolError
from oracle.services.research_tools.shelf import Shelf, fetch, symbol
from oracle.services.research_tools.web import need_contact
from oracle.store.db import to_utc_iso
from oracle.store.sec import SecTickerRepo

# SEC's ticker map changes slowly; a week-old copy only misses brand-new listings.
TICKERS_MAX_AGE = timedelta(days=7)


class NoFilings(ToolError):
    """SEC has nothing for the ticker, said plainly to the model."""


class Filings:
    name = "filings"
    description = ("a US-listed company's recent SEC filings (annual, quarterly and event reports), dated; "
                   "input: its symbol, or empty for the company researched")

    def __init__(self, client: RateLimitedClient, conn: sqlite3.Connection, clock: Clock, shelf: Shelf,
                 listing: str, contact: str | None) -> None:
        self._client = client
        self._tickers = SecTickerRepo(conn)
        self._clock = clock
        self._shelf = shelf
        self._listing = listing
        self._contact = contact

    async def run(self, input: str) -> str:
        ticker = symbol(input, self._listing).upper()
        try:
            filings = await self._lookup(ticker)
        except NoFilings as exc:
            return str(exc)
        return self._shelf.show([_article(f) for f in filings], "sec_edgar", ticker)

    async def to_read(self, day: date) -> dict[int, str]:
        """The listing's filings worth reading by `day`, kept as the run's evidence: id -> form,
        in reading order. Empty when SEC has none or cannot be reached, so the run goes on."""
        try:
            filings = edgar.worth_reading(await self._lookup(self._listing.upper()), day)
        except ToolError:
            return {}
        ids = self._shelf.keep([_article(f) for f in filings], "sec_edgar", self._listing.upper())
        return dict(zip(ids, (f.form for f in filings)))

    async def _lookup(self, ticker: str) -> list[edgar.Filing]:
        # Yahoo marks non-US listings with an exchange suffix (VOD.L, 7203.T); US share
        # classes use a dash (BRK-B).
        if "." in ticker:
            raise NoFilings(f"No SEC filings: {ticker} is not a US listing.")
        headers = {"User-Agent": edgar.user_agent(need_contact(self._contact))}
        await self._refresh_tickers(headers)
        found = self._tickers.lookup(ticker) or self._tickers.lookup(ticker.replace("-", "."))
        if found is None:
            raise NoFilings(f"No SEC filer with ticker {ticker}.")
        cik, _ = found
        return edgar.parse_filings(await self._json(edgar.submissions_url(cik), headers), cik)

    async def _refresh_tickers(self, headers: dict) -> None:
        fetched = self._tickers.fetched_at()
        if fetched and self._clock.utcnow() - datetime.fromisoformat(fetched) < TICKERS_MAX_AGE:
            return
        tickers = edgar.parse_tickers(await self._json(edgar.TICKERS_URL, headers))
        if tickers:
            self._tickers.replace(tickers, to_utc_iso(self._clock.utcnow()))

    async def _json(self, url: str, headers: dict) -> object:
        resp = await fetch(self._client, "sec_edgar", "SEC", url, headers=headers)
        try:
            return resp.json()
        except ValueError as exc:
            raise ToolError(f"SEC sent something unreadable ({exc})") from exc


def _article(f: edgar.Filing) -> Article:
    return Article(f.url, f.title, "SEC", datetime.combine(f.filed, time(), timezone.utc))
