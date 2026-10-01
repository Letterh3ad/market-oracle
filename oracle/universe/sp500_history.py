"""S&P 500 membership as it was on each day, from fja05680/sp500 (MIT): a CSV whose every row is
the whole member list from its date until the next row's. Also the GICS sectors of today's
members from Wikipedia's member table, and Yahoo's sector names put in GICS's words."""

from __future__ import annotations

import csv
import io
import re
from datetime import date

from oracle.universe.indexes import Unreadable, wiki_tables

REPO = "fja05680/sp500"
SOURCE = "github"
COMMIT_URL = f"https://api.github.com/repos/{REPO}/commits/master"
FILE = "S&P 500 Historical Components & Changes (Updated).csv"
FILE_URL = "https://raw.githubusercontent.com/" + REPO + "/{rev}/S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"

_NOTE = re.compile(r"\[[^\]]*\]")
GICS_SECTORS = ("Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
                "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities")
YAHOO_TO_GICS = {
    "Technology": "Information Technology", "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples", "Financial Services": "Financials", "Healthcare": "Health Care",
    "Basic Materials": "Materials", "Communication Services": "Communication Services", "Energy": "Energy",
    "Industrials": "Industrials", "Real Estate": "Real Estate", "Utilities": "Utilities",
}


def yahoo_ticker(ticker: str) -> str:
    """A US share class as Yahoo spells it: BRK.B is BRK-B."""
    return ticker.strip().upper().replace(".", "-")


def parse_history(text: str) -> list[tuple[date, tuple[str, ...]]]:
    """(day, members in Yahoo's spelling) per row, oldest first."""
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
        out = [(date.fromisoformat(r["date"]), tuple(sorted({yahoo_ticker(t) for t in r["tickers"].split(",")
                                                              if t.strip()})))
               for r in rows]
    except (KeyError, ValueError, AttributeError, csv.Error) as exc:
        raise Unreadable(f"S&P 500 history: unreadable CSV ({type(exc).__name__}: {exc})") from None
    if not out:
        raise Unreadable("S&P 500 history: the CSV has no rows")
    return sorted(out)


def parse_sectors(html: str) -> dict[str, str]:
    """Yahoo ticker -> GICS sector, from the Wikipedia table with Symbol and GICS Sector columns."""
    for rows in wiki_tables(html):
        head = [_NOTE.sub("", c).strip().casefold() for c in rows[0]] if rows else []
        if "symbol" in head and "gics sector" in head:
            s, g = head.index("symbol"), head.index("gics sector")
            return {yahoo_ticker(r[s]): r[g] for r in rows[1:] if max(s, g) < len(r) and r[s] and r[g] in GICS_SECTORS}
    raise Unreadable("S&P 500: no table with Symbol and GICS Sector columns")
