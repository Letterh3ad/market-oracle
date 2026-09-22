import asyncio
from datetime import datetime, timezone

import pytest

from oracle.config import SourceConfig
from oracle.net.errors import CircuitOpen
from oracle.net.gate import SourceGate
from oracle.store.db import connect
from oracle.store.migrate import migrate
from tests.fakes.clock import FakeClock

START = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
SOURCES = {
    "slow": SourceConfig(rate_per_second=0.5, cooldown_s=900.0),
    "fast": SourceConfig(rate_per_second=10.0, cooldown_s=60.0),
}


@pytest.fixture
def clock():
    return FakeClock(start_utc=START)


@pytest.fixture
def gate(conn, clock):
    return SourceGate(conn, clock, SOURCES)


async def test_second_request_to_a_source_waits_one_interval(gate, clock):
    await gate.acquire("slow")
    await gate.acquire("slow")
    assert clock.sleeps == [2.0]


async def test_sources_do_not_throttle_each_other(gate, clock):
    await gate.acquire("slow")
    await gate.acquire("fast")
    assert clock.sleeps == []


async def test_idle_time_does_not_bank_a_burst(gate, clock):
    await gate.acquire("slow")
    clock.advance(3600)
    await gate.acquire("slow")
    await gate.acquire("slow")
    assert clock.sleeps == [2.0]


async def test_concurrent_requests_queue_one_interval_apart(gate, clock):
    await asyncio.gather(*(gate.acquire("slow") for _ in range(3)))
    assert clock.now() == pytest.approx(4.0)


async def test_a_run_of_429s_opens_the_breaker_until_cooldown(gate, clock):
    for _ in range(3):
        gate.record("fast", 429)
    with pytest.raises(CircuitOpen, match="429"):
        await gate.acquire("fast")
    clock.advance(60)
    await gate.acquire("fast")


async def test_a_run_of_403s_opens_the_breaker(gate):
    for _ in range(3):
        gate.record("fast", 403)
    with pytest.raises(CircuitOpen, match="403"):
        await gate.acquire("fast")


async def test_a_run_of_5xx_opens_the_breaker(gate):
    for _ in range(5):
        gate.record("fast", 503)
    with pytest.raises(CircuitOpen, match="5xx"):
        await gate.acquire("fast")


async def test_a_success_resets_the_runs(gate):
    for _ in range(2):
        gate.record("fast", 429)
    gate.record("fast", 200)
    for _ in range(2):
        gate.record("fast", 429)
    await gate.acquire("fast")


async def test_a_breaker_on_one_source_leaves_others_open(gate):
    for _ in range(3):
        gate.record("fast", 429)
    await gate.acquire("slow")


async def test_defer_pushes_the_next_slot_back(gate, clock):
    await gate.acquire("fast")
    gate.defer("fast", 30.0)
    await gate.acquire("fast")
    assert clock.sleeps == [pytest.approx(30.0)]


async def test_an_unconfigured_source_is_refused(gate):
    with pytest.raises(KeyError, match="nope"):
        await gate.acquire("nope")


async def test_breaker_and_deferral_survive_a_new_process(tmp_path, clock):
    path = tmp_path / "shared.db"
    first = connect(path)
    migrate(first)
    gate = SourceGate(first, clock, SOURCES)
    gate.defer("slow", 100.0)
    for _ in range(3):
        gate.record("fast", 429)
    first.close()

    second = connect(path)
    restarted = SourceGate(second, clock, SOURCES)
    with pytest.raises(CircuitOpen):
        await restarted.acquire("fast")
    await restarted.acquire("slow")
    assert clock.sleeps == [pytest.approx(100.0)]
    second.close()


async def test_after_cooldown_one_failed_probe_reopens_the_breaker(gate, clock):
    for _ in range(3):
        gate.record("fast", 403)
    clock.advance(60)
    await gate.acquire("fast")
    gate.record("fast", 403)
    with pytest.raises(CircuitOpen):
        await gate.acquire("fast")


async def test_after_cooldown_a_successful_probe_fully_closes_the_breaker(gate, clock):
    for _ in range(3):
        gate.record("fast", 403)
    clock.advance(60)
    await gate.acquire("fast")
    gate.record("fast", 200)
    for _ in range(2):
        gate.record("fast", 403)
    await gate.acquire("fast")
