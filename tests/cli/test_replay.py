import json

import pytest

from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from oracle.store.replay import ReplayRepo
from oracle.store.universe import UniverseRepo
from tests.services.test_replay import SPREAD


@pytest.fixture
def cfg(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        UniverseRepo(c.conn).replace_all(SPREAD)
    path = tmp_path / "oracle.toml"
    path.write_text('db_path = "o.db"\n', encoding="utf-8")
    return str(path)


def replay(cfg, *args):
    return main(["--config", cfg, "replay", *args])


def test_create_start_pause_and_status(cfg, capsys):
    assert replay(cfg, "create", "--from", "2026-06-01") == 0
    out = capsys.readouterr().out
    assert "Created replay study 1: 7 listings" in out and "It is paused" in out and "PETR4.SA" in out
    assert replay(cfg, "start") == 0
    replay(cfg, "status", "--json")
    s = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert (s["state"], s["done"], s["failed"], s["listings"][0]["symbol"]) == ("running", 0, [], "AAPL")
    assert s["first_day"] == "2026-06-01" and s["pending"] == s["total"]
    assert replay(cfg, "pause") == 0
    capsys.readouterr()
    replay(cfg, "status")
    out = capsys.readouterr().out
    assert "Replay study 1, 2026-06-01" in out and ": paused" in out and "listings: AAPL, JPM" in out


def test_without_a_study_status_says_how_to_create_one(cfg, capsys):
    replay(cfg, "status")
    assert "No replay study yet" in capsys.readouterr().out
    replay(cfg, "status", "--json")
    assert capsys.readouterr().out.strip() == "null"


def test_create_without_from_or_a_cutoff_is_refused(cfg, capsys):
    assert replay(cfg, "create") == 1
    assert "--from" in capsys.readouterr().err


def test_retry_all_or_one_failed_cell(cfg, capsys, tmp_path):
    replay(cfg, "create", "--from", "2026-06-01")
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        repo = ReplayRepo(c.conn)
        a, b, d = repo.cells(1)[:3]
        for cell in (a, b, d):
            repo.mark_failed(cell.id, "boom", repo.current().created_at, requeue=False)
    capsys.readouterr()
    assert replay(cfg, "retry", str(b.id)) == 0
    assert "1 failed cell back" in capsys.readouterr().out
    assert replay(cfg, "retry") == 0
    assert "2 failed cells back" in capsys.readouterr().out
