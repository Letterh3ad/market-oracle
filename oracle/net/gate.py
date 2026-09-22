from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from oracle.clock import Clock
from oracle.config import SourceConfig
from oracle.net.errors import CircuitOpen
from oracle.store.db import transaction
from oracle.store.sources import SourceState, SourceStateRepo

MAX_429_RUN = 3
MAX_403_RUN = 3
MAX_5XX_RUN = 5


@dataclass(frozen=True)
class SourceStatus:
    source: str
    rate_per_second: float
    requests_last_hour: int
    open_until: float | None
    reason: str
    deferred_s: float


class SourceGate:
    """Paces and guards requests per source, with state in SQLite so every process
    sharing the database shares one budget.

    Each acquirer claims the next free slot atomically (BEGIN IMMEDIATE serialises
    processes), then sleeps outside the transaction. No burst credit accrues while idle:
    a burst is what trips a server-side limiter even when the average rate is legal.
    """

    def __init__(
        self, conn: sqlite3.Connection, clock: Clock, sources: Mapping[str, SourceConfig]
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._sources = sources
        self._repo = SourceStateRepo(conn)

    def config(self, source: str) -> SourceConfig:
        try:
            return self._sources[source]
        except KeyError:
            raise KeyError(f"source {source!r} is not configured") from None

    async def acquire(self, source: str) -> None:
        cfg = self.config(source)
        now = self._now()
        with transaction(self._conn):
            state = self._repo.load(source)
            self._check_breaker(state, cfg, now)
            slot = max(now, state.next_allowed)
            state.next_allowed = slot + 1.0 / cfg.rate_per_second
            self._repo.save(state)
            self._repo.log_request(source, slot)
        if slot > now:
            await self._clock.sleep(slot - now)

    def defer(self, source: str, seconds: float) -> None:
        self.config(source)
        now = self._now()
        with transaction(self._conn):
            state = self._repo.load(source)
            state.next_allowed = max(state.next_allowed, now + seconds)
            self._repo.save(state)

    def record(self, source: str, status: int) -> None:
        if status == 429:
            self._bump(source, "run_429", MAX_429_RUN, "consecutive 429 responses")
        elif status == 403:
            # A run of 403s is what an IP restriction looks like from here.
            self._bump(source, "run_403", MAX_403_RUN, "consecutive 403 responses")
        elif status >= 500:
            self.record_transport_error(source)
        else:
            self._reset_runs(source)

    def record_transport_error(self, source: str) -> None:
        self._bump(source, "run_5xx", MAX_5XX_RUN, "consecutive 5xx or transport failures")

    def status(self, source: str) -> SourceStatus:
        cfg = self.config(source)
        now = self._now()
        state = self._repo.load(source)
        open_until = None
        if state.tripped_at is not None and now < state.tripped_at + cfg.cooldown_s:
            open_until = state.tripped_at + cfg.cooldown_s
        return SourceStatus(
            source=source,
            rate_per_second=cfg.rate_per_second,
            requests_last_hour=self._repo.recent_requests(source, now),
            open_until=open_until,
            reason=state.reason if open_until else "",
            deferred_s=max(0.0, state.next_allowed - now - 1.0 / cfg.rate_per_second),
        )

    def _bump(self, source: str, run: str, limit: int, what: str) -> None:
        self.config(source)
        now = self._now()
        with transaction(self._conn):
            state = self._repo.load(source)
            # Each run counts independently: zeroing the others let a server alternating
            # between rate limiting and failing never trip anything.
            count = getattr(state, run) + 1
            setattr(state, run, count)
            # Responses in flight when the breaker opened must not push the cooldown back.
            if count >= limit and state.tripped_at is None:
                state.tripped_at = now
                state.reason = f"{count} {what}"
            self._repo.save(state)

    def _reset_runs(self, source: str) -> None:
        self.config(source)
        with transaction(self._conn):
            state = self._repo.load(source)
            state.run_429 = state.run_5xx = state.run_403 = 0
            self._repo.save(state)

    def _check_breaker(self, state: SourceState, cfg: SourceConfig, now: float) -> None:
        if state.tripped_at is None:
            return
        retry_at = state.tripped_at + cfg.cooldown_s
        if now < retry_at:
            raise CircuitOpen(state.source, state.reason, retry_at)
        state.tripped_at = None
        state.reason = ""
        state.run_429 = state.run_5xx = state.run_403 = 0

    def _now(self) -> float:
        return self._clock.utcnow().timestamp()
