"""FinanceDatabase (MIT, github.com/JerBouma/FinanceDatabase) read straight from its
compressed CSVs, pinned to one commit so a refresh never mixes two versions."""

from __future__ import annotations

import bz2
import csv
import io
from dataclasses import replace

from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.universe.listings import Listing

SOURCE = "financedatabase"
REPO = "JerBouma/FinanceDatabase"
COMMIT_URL = f"https://api.github.com/repos/{REPO}/commits/main"
FILE_URL = "https://raw.githubusercontent.com/" + REPO + "/{sha}/compression/{name}"

EQUITY_COLUMNS = (
    "symbol", "name", "summary", "currency", "sector", "industry_group", "industry",
    "exchange", "market", "country", "website", "market_cap", "isin", "delisted",
)


ETF_COLUMNS = (
    "symbol", "name", "currency", "summary", "category_group", "category", "family",
    "exchange", "isin",
)


class Unavailable(Exception):
    pass


async def latest_commit(client: RateLimitedClient) -> str:
    resp = await _get(client, COMMIT_URL, headers={"Accept": "application/vnd.github+json"})
    try:
        sha = resp.json()["sha"]
    except (ValueError, KeyError, TypeError):
        raise Unavailable(f"could not read the latest {REPO} commit: {resp.text[:200]}") from None
    if not isinstance(sha, str) or not sha:
        raise Unavailable(f"could not read the latest {REPO} commit: {sha!r}")
    return sha


async def download(client: RateLimitedClient, sha: str, name: str) -> bytes:
    return (await _get(client, FILE_URL.format(sha=sha, name=name))).content


def parse_equities(data: bytes) -> list[Listing]:
    return with_missing_home_lines(_listings(_rows(data, "equities.bz2", EQUITY_COLUMNS), "equity"))


MISSING_HOME_LINES: dict[str, tuple[str, str, str, str]] = {
    # FinanceDatabase has TSMC's New York ADR and European quotes, but no Taipei line.
    "2330.TW": ("TSM", "TAI", "TWD", "Taiwan Stock Exchange"),
}
"""Home lines FinanceDatabase lacks: symbol -> (a line of the same company it has, exchange,
currency, market). Added as copies of that line, so they group with it."""


def with_missing_home_lines(listings: list[Listing]) -> list[Listing]:
    """`listings` plus each MISSING_HOME_LINES line it lacks whose company it has."""
    by_symbol = {x.symbol: x for x in listings}
    return listings + [replace(by_symbol[source], symbol=symbol, exchange=exchange, currency=currency, market=market)
                       for symbol, (source, exchange, currency, market) in MISSING_HOME_LINES.items()
                       if symbol not in by_symbol and source in by_symbol]


def parse_etfs(data: bytes) -> list[Listing]:
    return _listings(_rows(data, "etfs.bz2", ETF_COLUMNS), "etf")


# Columns copied as-is when a file has them; blanks become None.
_OPTIONAL = (
    "country", "exchange", "market", "currency", "isin", "sector", "industry_group",
    "industry", "market_cap", "website", "summary", "category_group", "category", "family",
)


def _listings(rows: list[dict], type_: str) -> list[Listing]:
    seen: set[str] = set()
    out = []
    for r in rows:
        symbol = r["symbol"].strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(Listing(
            symbol=symbol,
            name=r["name"].strip(),
            type=type_,
            delisted=r.get("delisted") == "True",
            **{c: (r.get(c) or "").strip() or None for c in _OPTIONAL},
        ))
    return out


def _rows(data: bytes, name: str, required: tuple[str, ...]) -> list[dict]:
    try:
        text = bz2.decompress(data).decode("utf-8")
    except (OSError, EOFError, ValueError) as exc:
        raise Unavailable(f"{name} is not a readable bz2 CSV ({exc})") from None
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise Unavailable(f"{name} has changed format: missing column(s) {', '.join(missing)}")
    return list(reader)


async def _get(client: RateLimitedClient, url: str, headers: dict | None = None):
    try:
        resp = await client.get(SOURCE, url, headers=headers)
    except (RequestFailed, CircuitOpen) as exc:
        raise Unavailable(str(exc)) from exc
    if resp.status_code != 200:
        raise Unavailable(f"{url} returned HTTP {resp.status_code}")
    return resp
