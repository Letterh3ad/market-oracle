from datetime import datetime, timedelta, timezone

from tests.fakes.clock import FakeClock
from oracle.clock import SystemClock


def test_system_clock_utcnow_is_timezone_aware():
    assert SystemClock().utcnow().tzinfo is not None


async def test_system_clock_sleeps():
    clock = SystemClock()
    before = clock.now()
    await clock.sleep(0.01)
    assert clock.now() - before >= 0.005


async def test_fake_clock_sleep_advances_without_waiting():
    start_utc = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
    clock = FakeClock(start_utc=start_utc)
    await clock.sleep(3600)
    assert clock.now() == 3600
    assert clock.utcnow() == start_utc + timedelta(hours=1)


def test_fake_clock_advance_moves_both_clocks():
    clock = FakeClock(start_utc=datetime(2026, 8, 27, tzinfo=timezone.utc))
    clock.advance(90)
    assert clock.now() == 90
    assert clock.utcnow().minute == 1


async def test_fake_clock_records_sleeps_for_assertions():
    clock = FakeClock(start_utc=datetime(2026, 8, 27, tzinfo=timezone.utc))
    await clock.sleep(2)
    await clock.sleep(4)
    assert clock.sleeps == [2, 4]
