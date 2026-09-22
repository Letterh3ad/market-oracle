from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from oracle.market import registry
from oracle.market.fundamentals import Fundamentals
from oracle.market.sources import AllFailed, Instrument, first_success
from oracle.services.context import AppContext
from oracle.services.http import open_client
from oracle.store.db import to_utc_iso
from oracle.store.fundamentals import FundamentalsRepo

# A peer is context, not the subject: a week-old snapshot is close enough.
PEER_MAX_AGE = timedelta(days=7)


async def fetch_fundamentals(
    ctx: AppContext, instruments: Sequence[Instrument], max_age: timedelta = timedelta(0)
) -> dict[str, tuple[Fundamentals | None, str | None]]:
    """(snapshot, note) per symbol: the stored one while younger than `max_age` (0 means
    fetched today), else a fresh one. A failed fetch falls back to the stored snapshot and
    says so; one failure never stops the rest. One client and crumb serve them all."""
    repo = FundamentalsRepo(ctx.conn)
    today = ctx.clock.utcnow().date()
    out: dict[str, tuple[Fundamentals | None, str | None]] = {}
    due = []
    for instrument in instruments:
        stored = repo.latest(instrument.symbol)
        if stored is not None and today - stored.as_of <= max_age:
            out[instrument.symbol] = (stored, None)
        else:
            due.append((instrument, stored))
    if not due:
        return out
    async with open_client(ctx) as client:
        chain = registry.fundamentals_sources(ctx.config.prices.sources, client, ctx.config.keys)
        for instrument, stored in due:
            try:
                fresh, _ = await first_success(chain, lambda s: s.fundamentals(instrument, today))
            except AllFailed as exc:
                if stored is not None:
                    out[instrument.symbol] = (stored, f"could not refresh ({exc}); showing {stored.as_of.isoformat()}")
                else:
                    out[instrument.symbol] = (None, f"no fundamentals ({exc})")
                continue
            repo.save(fresh, to_utc_iso(ctx.clock.utcnow()))
            out[instrument.symbol] = (fresh, None)
    return out
