"""FinanceDatabase (MIT, github.com/JerBouma/FinanceDatabase) read straight from its
compressed CSVs, pinned to one commit so a refresh never mixes two versions."""

from __future__ import annotations

import bz2
import csv
import io

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
    rows = _rows(data, "equities.bz2", EQUITY_COLUMNS)
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
            type="equity",
            delisted=r["delisted"] == "True",
            **{c: r[c].strip() or None for c in (
                "country", "exchange", "market", "currency", "isin", "sector",
                "industry_group", "industry", "market_cap", "website", "summary",
            )},
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
