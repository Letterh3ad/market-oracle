import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFRESHER = Path(__file__).with_name("_refresher.py")


def test_a_second_refresh_process_is_refused_while_the_first_runs(tmp_path):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    ready, release = tmp_path / "ready", tmp_path / "release"
    first = subprocess.Popen(
        [sys.executable, "-m", "tests._refresher", str(cfg), str(ready), str(release)], cwd=ROOT
    )
    try:
        deadline = time.time() + 30
        while not ready.exists():
            assert first.poll() is None, "first refresh exited early"
            assert time.time() < deadline, "first refresh never reached the download"
            time.sleep(0.05)

        second = subprocess.run(
            [sys.executable, "-m", "oracle", "--config", str(cfg), "universe", "refresh"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        assert second.returncode == 1, second.stdout + second.stderr
        assert "universe refresh is already running" in second.stderr

        release.write_text("go")
        assert first.wait(timeout=30) == 0
    finally:
        if first.poll() is None:
            first.kill()
    status = subprocess.run(
        [sys.executable, "-m", "oracle", "--config", str(cfg), "universe", "status"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert "7 listings" in status.stdout
