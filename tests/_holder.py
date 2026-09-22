"""Subprocess body for the hard-kill test: take a long lease, signal ready, hang."""

import sys
import time
from pathlib import Path

from oracle.clock import SystemClock
from oracle.jobs import JobLock
from oracle.store.db import connect

if __name__ == "__main__":
    db, ready = sys.argv[1:]
    JobLock(connect(db), SystemClock()).acquire("tick", lease_s=600)
    Path(ready).write_text("ok")
    time.sleep(600)
