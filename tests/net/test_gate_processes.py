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

    # Spacing is judged on the slots the gate handed out: wake-up times after the sleep
    # carry scheduler jitter, so under load two can land closer than the gate spaced them.
    conn = connect(db)
    slots = [r[0] for r in conn.execute("SELECT at FROM source_requests WHERE source = 'shared' ORDER BY at")]
    conn.close()
    interval = 1.0 / RATE
    assert len(slots) == 2 * PER_PROCESS
    assert min(b - a for a, b in zip(slots, slots[1:])) >= interval - 1e-6
    # Nobody went early: each process woke no sooner than its own slots allow.
    stamps = sorted(float(x) for out in outs for x in out.read_text().split())
    assert stamps[-1] - stamps[0] >= (len(stamps) - 1) * interval - TIMER_SLACK
