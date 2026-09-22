import subprocess
import sys
import time
from pathlib import Path

from oracle.store.db import connect
from oracle.store.migrate import migrate

HAMMER = Path(__file__).with_name("_hammer.py")
ROOT = Path(__file__).resolve().parents[2]
RATE = 20.0
PER_PROCESS = 10
# Windows timers can wake a sleeper up to one ~15ms tick early.
TIMER_SLACK = 0.02


def test_two_processes_share_one_sources_rate(tmp_path):
    db = tmp_path / "shared.db"
    conn = connect(db)
    migrate(conn)
    conn.close()

    start_at = time.time() + 3.0  # both interpreters are up before anyone acquires
    outs = [tmp_path / f"p{i}.txt" for i in range(2)]
    procs = [
        subprocess.Popen(
            [sys.executable, str(HAMMER), str(db), str(RATE), str(PER_PROCESS), repr(start_at), str(out)],
            cwd=ROOT,
        )
        for out in outs
    ]
    for p in procs:
        assert p.wait(timeout=60) == 0

    stamps = sorted(float(x) for out in outs for x in out.read_text().split())
    interval = 1.0 / RATE
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 2 * PER_PROCESS
    assert min(gaps) >= interval - TIMER_SLACK
    assert stamps[-1] - stamps[0] >= (len(stamps) - 1) * interval - TIMER_SLACK
