import os
import subprocess
import sys
import time
from pathlib import Path

from oracle.clock import SystemClock
from oracle.jobs import JobLock, pid_alive
from oracle.store.db import connect
from oracle.store.migrate import migrate

ROOT = Path(__file__).resolve().parents[1]
HOLDER = Path(__file__).with_name("_holder.py")


def test_pid_alive_sees_this_process():
    assert pid_alive(os.getpid())


def test_pid_alive_is_false_for_a_killed_child_whose_handle_we_still_hold():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    assert pid_alive(child.pid)
    child.kill()
    child.wait(timeout=10)
    assert not pid_alive(child.pid)


def test_a_hard_killed_holder_is_taken_over_without_waiting_for_its_lease(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    migrate(conn)
    ready = tmp_path / "ready"
    child = subprocess.Popen([sys.executable, str(HOLDER), str(db), str(ready)], cwd=ROOT)
    try:
        deadline = time.time() + 30
        while not ready.exists():
            assert time.time() < deadline, "holder never took the lock"
            time.sleep(0.05)
        child.kill()
        child.wait(timeout=10)
        handle = JobLock(conn, SystemClock()).acquire("tick", lease_s=60)
        assert handle.pid == os.getpid()
    finally:
        if child.poll() is None:
            child.kill()
        conn.close()
