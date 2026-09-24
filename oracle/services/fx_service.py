from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.config import FX_SOURCES
from oracle.market import registry
from oracle.market.bars import is_stale
from oracle.market.fx import OPEN_ER_ATTRIBUTION, FxRate, OpenErFx
from oracle.market.sources import REFETCH_OVERLAP, AllFailed, first_success
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.http import open_client
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.fx import FxRepo

__all__ = ["FX_SOURCES", "FxLatest", "FxRate", "FxUpdate", "check_sources", "credits", "fx_history", "fx_latest",
           "update_fx"]

HISTORY_RETRY = timedelta(days=7)


@dataclass(frozen=True)
class FxUpdate:
    currency: str
    added: int
    source: str | None
    """None when no source had rates."""
    error: str | None


@dataclass(frozen=True)
class FxLatest:
    currency: str
    rate: FxRate | None
    age_days: int | None
    stale: bool
    """No rate, or none for more than a couple of weekdays."""


def check_sources(sources: Sequence[str]) -> None:
    unknown = [n for n in sources if n not in FX_SOURCES]
    if unknown:
        raise InvalidQuery(f"unknown FX source {', '.join(unknown)}; known: {', '.join(FX_SOURCES)}")


async def update_fx(
    ctx: AppContext,
    sources: Sequence[str] | None = None,
    currencies: Sequence[str] | None = None,
    history: bool = True,
) -> list[FxUpdate]:
    """Fetch new daily rates into the base currency, for `currencies` or else every
    currency stored prices use. A currency no source covers is reported, not raised, so
    the rest still update. `history=False` fetches recent rates only, for a one-off
    conversion, and leaves full history for a later update."""
    check_sources(sources or ())
    base = ctx.config.base_currency
    repo = FxRepo(ctx.conn, base)
    bars = BarRepo(ctx.conn)
    wanted = [c.upper() for c in currencies] if currencies else bars.currencies()
    out = []
    async with open_client(ctx) as client:
        chain = registry.fx_sources(sources or ctx.config.fx.sources, client, ctx.config.keys)
        has_history = {s.name: s.has_history for s in chain}
        for currency in [c for c in wanted if c != base]:
            now = ctx.clock.utcnow()
            since = _since(repo, currency, now.date(), history)
            try:
                rates, source = await first_success(
                    chain, lambda s: s.daily_rates(base, currency, since, now), empty_is_no_data=since is None
                )
            except AllFailed as exc:
                out.append(FxUpdate(currency, 0, None, str(exc)))
                if since is None:
                    repo.note_history(currency, now.date(), filled=False)
                continue
            out.append(FxUpdate(currency, repo.upsert(rates, to_utc_iso(now)), source, None))
            if since is None:
                repo.note_history(currency, now.date(), filled=has_history[source])
    return out


def fx_history(ctx: AppContext, currency: str, days: int | None = None) -> list[FxRate]:
    return FxRepo(ctx.conn, ctx.config.base_currency).history(currency.upper(), days)


def fx_latest(ctx: AppContext) -> list[FxLatest]:
    """Each currency stored prices use, besides the base, with its newest rate."""
    base = ctx.config.base_currency
    repo = FxRepo(ctx.conn, base)
    today = ctx.clock.utcnow().date()
    out = []
    for currency in [c for c in BarRepo(ctx.conn).currencies() if c != base]:
        rate = next(iter(repo.history(currency, 1)), None)
        out.append(FxLatest(currency, rate, None if rate is None else (today - rate.date).days,
                            rate is None or is_stale(rate.date, today)))
    return out


def credits(sources: Iterable[str | None]) -> list[str]:
    """Credits the named rate sources require wherever their rates, or values converted
    with them, are shown."""
    return [OPEN_ER_ATTRIBUTION] if OpenErFx.name in set(sources) else []


def _since(repo: FxRepo, currency: str, today: date, history: bool) -> date | None:
    """Where to resume; None asks for full history. Until a history-capable source has
    filled it, full history is retried at most weekly: a currency only today-only
    open.er-api covers would otherwise ask every source for years of rates each run."""
    last = repo.last_date(currency)
    filled, tried_on = repo.history_state(currency)
    if filled and last is not None:
        return last - REFETCH_OVERLAP
    if history and (tried_on is None or today - tried_on >= HISTORY_RETRY):
        return None
    return (last or today) - REFETCH_OVERLAP
