from datetime import datetime, timezone

import pytest

from oracle.jobs import JobBusy, JobLock
from tests.fakes.clock import FakeClock

START = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


class Pids:
    def __init__(self):
        self.alive = set()

    def __call__(self, pid):
        return pid in self.alive


@pytest.fixture
def clock():
    return FakeClock(start_utc=START)


@pytest.fixture
def pids():
    p = Pids()
    p.alive.update({100, 200})
    return p


def _lock(conn, clock, pids, pid):
    return JobLock(conn, clock, pid_alive=pids, pid=pid, host="box")


def test_a_held_job_refuses_a_second_acquirer_naming_the_holder(conn, clock, pids):
    _lock(conn, clock, pids, 100).acquire("tick", lease_s=60)
    with pytest.raises(JobBusy) as err:
        _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)
    assert err.value.pid == 100
    assert "100" in str(err.value)


def test_different_jobs_do_not_block_each_other(conn, clock, pids):
    _lock(conn, clock, pids, 100).acquire("tick", lease_s=60)
    _lock(conn, clock, pids, 200).acquire("research", lease_s=60)


def test_a_dead_holder_is_taken_over(conn, clock, pids):
    _lock(conn, clock, pids, 100).acquire("tick", lease_s=60)
    pids.alive.discard(100)
    handle = _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)
    assert handle.pid == 200


def test_an_expired_lease_is_taken_over_even_if_the_pid_looks_alive(conn, clock, pids):
    # A live pid can be a reused one; the lease is the backstop.
    _lock(conn, clock, pids, 100).acquire("tick", lease_s=60)
    clock.advance(61)
    _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)


def test_a_heartbeat_extends_the_lease(conn, clock, pids):
    holder = _lock(conn, clock, pids, 100)
    handle = holder.acquire("tick", lease_s=60)
    clock.advance(50)
    assert holder.heartbeat(handle) is True
    clock.advance(50)
    with pytest.raises(JobBusy):
        _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)


def test_a_heartbeat_after_takeover_reports_the_lock_lost(conn, clock, pids):
    holder = _lock(conn, clock, pids, 100)
    handle = holder.acquire("tick", lease_s=60)
    clock.advance(61)
    _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)
    assert holder.heartbeat(handle) is False


def test_release_frees_the_job(conn, clock, pids):
    holder = _lock(conn, clock, pids, 100)
    holder.release(holder.acquire("tick", lease_s=60))
    _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)


def test_release_by_a_former_holder_is_a_no_op(conn, clock, pids):
    old = _lock(conn, clock, pids, 100)
    stale_handle = old.acquire("tick", lease_s=60)
    clock.advance(61)
    _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)
    old.release(stale_handle)
    with pytest.raises(JobBusy) as err:
        _lock(conn, clock, pids, 100).acquire("tick", lease_s=60)
    assert err.value.pid == 200


def test_held_releases_on_exit_and_on_error(conn, clock, pids):
    holder = _lock(conn, clock, pids, 100)
    with pytest.raises(RuntimeError):
        with holder.held("tick", lease_s=60):
            raise RuntimeError("boom")
    with holder.held("tick", lease_s=60):
        pass
    _lock(conn, clock, pids, 200).acquire("tick", lease_s=60)


def test_statuses_report_holder_age_and_staleness(conn, clock, pids):
    _lock(conn, clock, pids, 100).acquire("tick", lease_s=60)
    _lock(conn, clock, pids, 200).acquire("research", lease_s=600)
    pids.alive.discard(200)
    clock.advance(30)
    rows = {s.job: s for s in _lock(conn, clock, pids, 1).statuses()}
    assert rows["tick"].pid == 100 and rows["tick"].age_s == pytest.approx(30)
    assert rows["tick"].stale_reason is None
    assert rows["research"].stale_reason == "holder process is gone"
