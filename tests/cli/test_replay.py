import json
from datetime import date, datetime, timezone

import pytest

from oracle.cli.main import main
from oracle.config import Config
from oracle.services import replay_score
from oracle.services.context import open_context
from oracle.services.prices_service import ListingOutcome
from oracle.store.bars import BarRepo
from oracle.store.replay import ReplayRepo
from oracle.store.universe import UniverseRepo
from tests.services.test_replay import SPREAD
from tests.services.test_replay_score import LISTINGS, bars, replay_run


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


def scored_study(tmp_path):
    """A study whose windows all closed in 2025, so the report needs no fetch whatever today is."""
    days = (date(2025, 6, 2), date(2025, 7, 1))
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        repo = ReplayRepo(c.conn)
        sid = repo.create(datetime(2025, 8, 1, tzinfo=timezone.utc), *days, json.dumps(LISTINGS[:2]),
                          [(p["symbol"], d) for d in days for p in LISTINGS[:2]])
        # Entries are the first closes after each as-of day: Jun 3 and Jul 2.
        closes = {"AAA": [100, 100, 110, 110, 121, 121, 121, 121], "BBB": [100, 100, 100, 100, 90, 90, 90, 90]}
        for s, values in closes.items():
            days_closes = dict(zip(("2025-06-02", "2025-06-03", "2025-07-01", "2025-07-02", "2025-08-01",
                                    "2025-09-02", "2025-10-01", "2025-12-31"), values))
            BarRepo(c.conn).upsert(bars(s, days_closes), "2026-01-01T00:00:00Z")
        at = datetime(2025, 8, 1, tzinfo=timezone.utc)
        for cell, call in zip(repo.cells(sid), [("buy", 0.9), ("sell", 0.7), ("buy", 0.8), None]):
            repo.mark_done(cell.id, replay_run(c, cell.symbol, cell.as_of, *(call or ())), at)


@pytest.fixture
def no_fetch(monkeypatch):
    async def fetch(*_):
        raise AssertionError("nothing is missing")

    monkeypatch.setattr(replay_score, "update_listings", fetch)


def test_report_prints_the_score_and_its_limits_without_fetching(cfg, capsys, tmp_path, no_fetch):
    scored_study(tmp_path)
    assert replay(cfg, "report") == 0
    out = capsys.readouterr().out
    assert "3 verdicts, 1 without a verdict, 0 failed, 0 still to run, of 4 replays" in out
    assert "Verdict mix: 2 buy, 0 hold, 1 sell (67% buys), mean confidence 0.80" in out
    assert "1 month: 3 scored, 0 pending, 0 without prices; hit rate 67%, 100% against the basket" in out
    assert "base rates: 50% of all windows rose, 50% beat the basket" in out
    assert "Study prices last updated: 20" in out  # the report's own check just ran
    assert "survivorship" in out and "GDELT" in out
    replay(cfg, "report", "--json")
    body = json.loads(capsys.readouterr().out)
    assert body["verdicts"] == 3 and body["horizons"][0]["horizon"] == "1m" and body["limits"]


def test_report_fetches_missing_bars_first_and_says_how_it_went(cfg, capsys, tmp_path, monkeypatch):
    async def fetch(c, symbols, since):
        return [ListingOutcome(s, 0, None, f"no price data for {s}") for s in symbols]

    monkeypatch.setattr(replay_score, "update_listings", fetch)
    scored_study(tmp_path)
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        c.conn.execute("DELETE FROM bars WHERE symbol = 'BBB'")
        c.conn.commit()
    assert replay(cfg, "report") == 0
    out = capsys.readouterr().out
    assert "Prices for BBB: failed (no price data for BBB)" in out and "No stored prices cover: BBB" in out
    replay(cfg, "report", "--json")
    captured = capsys.readouterr()
    assert json.loads(captured.out)["unpriced_listings"] == ["BBB"] and "BBB" in captured.err


def test_report_without_a_study(cfg, capsys, no_fetch):
    replay(cfg, "report")
    assert "No replay study yet" in capsys.readouterr().out
