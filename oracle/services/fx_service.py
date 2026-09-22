from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.market.fx import EcbFx, EodhdFx, FrankfurterFx, FxRate, FxSource, OpenErFx, YahooFx
from oracle.market.sources import NoData, SourceFailed
from oracle.net.client import RateLimitedClient
from oracle.config import FX_SOURCES
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.http import open_client
from oracle.services.sources import missing_key
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.fx import FxRepo

__all__ = ["FxRate", "FxUpdate", "fx_history", "update_fx"]

OVERLAP = timedelta(days=7)


@dataclass(frozen=True)
class FxUpdate:
    currency: str
    added: int
    source: str | None
    """None when no source had rates."""
    error: str | None


async def update_fx(ctx: AppContext, sources: Sequence[str] | None = None) -> list[FxUpdate]:
    """Fetch new daily rates into the base currency for every currency stored prices
    use. A currency no source covers is reported, not raised, so the rest still update."""
    unknown = [n for n in sources or () if n not in FX_SOURCES]
    if unknown:
        raise InvalidQuery(f"unknown FX source {', '.join(unknown)}; known: {', '.join(FX_SOURCES)}")
    base = ctx.config.base_currency
    repo = FxRepo(ctx.conn, base)
    currencies = [c for c in BarRepo(ctx.conn).currencies() if c != base]
    out = []
    async with open_client(ctx) as client:
        chain = _chain(ctx, client, sources or ctx.config.fx.sources)
        for currency in currencies:
            last = repo.last_date(currency)
            since = None if last is None else last - OVERLAP
            rates, source, error = await _first_with_rates(ctx, chain, base, currency, since)
            added = repo.upsert(rates, to_utc_iso(ctx.clock.utcnow())) if rates else 0
            out.append(FxUpdate(currency, added, source, error))
    return out


def fx_history(ctx: AppContext, currency: str, days: int | None = None) -> list[FxRate]:
    return FxRepo(ctx.conn, ctx.config.base_currency).history(currency.upper(), days)


async def _first_with_rates(ctx, chain: list[FxSource], base: str, currency: str, since: date | None):
    failures = []
    for source in chain:
        try:
            rates = await source.daily_rates(base, currency, since, ctx.clock.utcnow())
        except (NoData, SourceFailed) as exc:
            failures.append(f"{source.name}: {exc}")
            continue
        if rates or since is not None:
            return rates, source.name, None
        failures.append(f"{source.name}: no data")
    return [], None, "; ".join(failures) or "no FX source enabled"


def _chain(ctx: AppContext, client: RateLimitedClient, names: Sequence[str]) -> list[FxSource]:
    build = {
        "yahoo": lambda: YahooFx(client),
        "ecb": lambda: EcbFx(client),
        "frankfurter": lambda: FrankfurterFx(client),
        "eodhd": lambda: EodhdFx(client, ctx.config.keys.get("eodhd")),
        "open_er_api": lambda: OpenErFx(client),
    }
    return [build[name]() for name in names if not missing_key(ctx, name)]
