from oracle.cli.main import main
from oracle.clock import SystemClock
from oracle.config import load_config
from oracle.jobs import JobLock
from oracle.store.db import connect
from oracle.store.migrate import migrate


def _setup(tmp_path):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("", encoding="utf-8")
    conn = connect(load_config(cfg)[0].db_path)
    migrate(conn)
    return cfg, conn


def test_no_locks_says_so(tmp_path, capsys):
    cfg, _ = _setup(tmp_path)
    assert main(["--config", str(cfg), "jobs", "status"]) == 0
    assert "no jobs held" in capsys.readouterr().out


def test_live_and_stale_holders_are_distinguished(tmp_path, capsys):
    cfg, conn = _setup(tmp_path)
    JobLock(conn, SystemClock()).acquire("tick", lease_s=600)
    JobLock(conn, SystemClock(), pid=999_999_999).acquire("research", lease_s=600)
    main(["--config", str(cfg), "jobs", "status"])
    rows = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()[1:]}
    assert "held" in rows["tick"]
    assert "STALE" in rows["research"] and "process is gone" in rows["research"]
