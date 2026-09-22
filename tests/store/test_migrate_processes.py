import subprocess
import sys
import time
from pathlib import Path

from oracle.store.db import connect
from oracle.store.migrate import SCHEMA_VERSION, current_version

ROOT = Path(__file__).resolve().parents[2]
BODY = """
import sys, time
from oracle.store.db import connect
from oracle.store.migrate import migrate
db, start_at = sys.argv[1], float(sys.argv[2])
time.sleep(max(0.0, start_at - time.time()))
migrate(connect(db))
"""


def test_processes_racing_to_migrate_a_fresh_database_all_succeed(tmp_path):
    for attempt in range(4):
        db = tmp_path / f"race{attempt}.db"
        start_at = time.time() + 2.0
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", BODY, str(db), repr(start_at)],
                cwd=ROOT, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(10)
        ]
        errors = [p.communicate(timeout=60)[1] for p in procs]
        assert all(p.returncode == 0 for p in procs), "\n".join(e for e in errors if e)
        assert current_version(connect(db)) == SCHEMA_VERSION
