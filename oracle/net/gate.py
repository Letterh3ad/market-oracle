from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from oracle.clock import Clock, epoch
from oracle.config import SourceConfig
from oracle.net.errors import CircuitOpen
from oracle.store.db import transaction
from oracle.store.sources import SourceState, SourceStateRepo


@dataclass(frozen=True)
class _Run:
    field: str
    limit: int
    what: str


RUN_429 = _Run("run_429", 3, "consecutive 429 responses")
# A run of 403s is what an IP restriction looks like from here.
RUN_403 = _Run("run_403", 3, "consecutive 403 responses")
RUN_5XX = _Run("run_5xx", 5, "consecutive 5xx or transport failures")
RUNS = (RUN_429, RUN_403, RUN_5XX)


@dataclass(frozen=True)
class SourceStatus:
    source: str
    rate_per_second: float
    requests_last_hour: int
    open_until: float | None
    reason: str
    next_slot_in_s: float

    @property
    def state(self) -> str:
        """paused (breaker open), waiting (next slot further off than one interval) or ok."""
        if self.open_until is not None:
            return "paused"
        return "waiting" if self.next_slot_in_s > 1.0 / self.rate_per_second else "ok"


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
        with transaction(self._conn):
            # Read under the write lock: BEGIN IMMEDIATE can wait out another process,
            # and a stale `now` would claim a slot already in the past.
            now = epoch(self._clock)
            state = self._repo.load(source)
            self._check_breaker(state, cfg, now)
            slot = max(now, state.next_allowed)
            state.next_allowed = slot + 1.0 / cfg.rate_per_second
            self._repo.save(state)
            self._repo.log_request(source, slot)
        if slot > now:
            await self._clock.sleep(slot - now)

    def defer(self, source: str, seconds: float) -> None:
        self._require(source)
        with transaction(self._conn):
            state = self._repo.load(source)
            state.next_allowed = max(state.next_allowed, epoch(self._clock) + seconds)
            self._repo.save(state)

    def record(self, source: str, status: int) -> None:
        if status == 429:
            self._bump(source, RUN_429)
        elif status == 403:
            self._bump(source, RUN_403)
        elif status >= 500:
            self._bump(source, RUN_5XX)
        else:
            self._require(source)
            with transaction(self._conn):
                state = self._repo.load(source)
                _clear_runs(state)
                self._repo.save(state)

    def record_transport_error(self, source: str) -> None:
        self._bump(source, RUN_5XX)

    def status(self, source: str) -> SourceStatus:
        cfg = self.config(source)
        now = epoch(self._clock)
        state = self._repo.load(source)
        until = _open_until(state, cfg)
        is_open = until is not None and now < until
        return SourceStatus(
            source=source,
            rate_per_second=cfg.rate_per_second,
            requests_last_hour=self._repo.recent_requests(source, now),
            open_until=until if is_open else None,
            reason=state.reason if is_open else "",
            next_slot_in_s=max(0.0, state.next_allowed - now),
        )

    def _bump(self, source: str, run: _Run) -> None:
        self._require(source)
        with transaction(self._conn):
            now = epoch(self._clock)
            state = self._repo.load(source)
            # Each run counts independently: zeroing the others let a server alternating
            # between rate limiting and failing never trip anything.
            count = getattr(state, run.field) + 1
            setattr(state, run.field, count)
            # Responses in flight when the breaker opened must not push the cooldown back.
            if count >= run.limit and state.tripped_at is None:
                state.tripped_at = now
                state.reason = f"{count} {run.what}"
            self._repo.save(state)

    def _check_breaker(self, state: SourceState, cfg: SourceConfig, now: float) -> None:
        until = _open_until(state, cfg)
        if until is None:
            return
        if now < until:
            raise CircuitOpen(state.source, state.reason, until)
        # Half-open: leave the tripping run one short of its limit, so the first probe
        # that fails again re-opens the breaker instead of spending a whole new run
        # against a block that is still in place. A success clears it fully.
        state.tripped_at = None
        state.reason = ""
        for run in RUNS:
            setattr(state, run.field, min(getattr(state, run.field), run.limit - 1))

    def _require(self, source: str) -> None:
        self.config(source)


def _open_until(state: SourceState, cfg: SourceConfig) -> float | None:
    return None if state.tripped_at is None else state.tripped_at + cfg.cooldown_s


def _clear_runs(state: SourceState) -> None:
    for run in RUNS:
        setattr(state, run.field, 0)
