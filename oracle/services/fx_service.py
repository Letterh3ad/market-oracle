from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.config import FX_SOURCES
from oracle.market import registry
from oracle.market.fx import MAX_RATE_AGE, OPEN_ER_ATTRIBUTION, FxRate, OpenErFx
from oracle.market.sources import REFETCH_OVERLAP, AllFailed, first_success
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.http import open_client
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.fx import FxRepo

__all__ = ["FxRate", "FxUpdate", "credits", "fx_history", "update_fx"]


@dataclass(frozen=True)
class FxUpdate:
    currency: str
    added: int
    source: str | None
    """None when no source had rates."""
    error: str | None


async def update_fx(
    ctx: AppContext, sources: Sequence[str] | None = None, currencies: Sequence[str] | None = None
) -> list[FxUpdate]:
    """Fetch new daily rates into the base currency, for `currencies` or else every
    currency stored prices use. A currency no source covers is reported, not raised, so
    the rest still update."""
    unknown = [n for n in sources or () if n not in FX_SOURCES]
    if unknown:
        raise InvalidQuery(f"unknown FX source {', '.join(unknown)}; known: {', '.join(FX_SOURCES)}")
    base = ctx.config.base_currency
    repo = FxRepo(ctx.conn, base)
    bars = BarRepo(ctx.conn)
    wanted = [c.upper() for c in currencies] if currencies else bars.currencies()
    out = []
    async with open_client(ctx) as client:
        chain = registry.fx_sources(sources or ctx.config.fx.sources, client, ctx.config.keys)
        for currency in [c for c in wanted if c != base]:
            since = _since(repo, bars, currency)
            now = ctx.clock.utcnow()
            try:
                rates, source = await first_success(
                    chain, lambda s: s.daily_rates(base, currency, since, now), empty_is_no_data=since is None
                )
            except AllFailed as exc:
                out.append(FxUpdate(currency, 0, None, str(exc)))
                continue
            out.append(FxUpdate(currency, repo.upsert(rates, to_utc_iso(now)), source, None))
    return out


def fx_history(ctx: AppContext, currency: str, days: int | None = None) -> list[FxRate]:
    return FxRepo(ctx.conn, ctx.config.base_currency).history(currency.upper(), days)


def credits(sources: Iterable[str | None]) -> list[str]:
    """Credits the named rate sources require wherever their rates, or values converted
    with them, are shown."""
    return [OPEN_ER_ATTRIBUTION] if OpenErFx.name in set(sources) else []


def _since(repo: FxRepo, bars: BarRepo, currency: str) -> date | None:
    """Where to resume; None (full history) also when stored rates do not reach back to
    the oldest bar, as after a first run served by a today-only source."""
    last = repo.last_date(currency)
    if last is None:
        return None
    first_rate, first_bar = repo.first_date(currency), bars.first_date_in(currency)
    if first_bar is not None and first_rate - first_bar > MAX_RATE_AGE:
        return None
    return last - REFETCH_OVERLAP
