from datetime import date

from oracle.store import migrations
from oracle.store.db import connect
from oracle.store.migrate import migrate
from oracle.store.paper import DecisionRepo, LoggedDecision
from oracle.store.regime import RegimeStateRepo, StoredTilt, TiltRepo


def test_a_database_at_version_42_keeps_its_decisions_and_gains_regime_explanations_and_states(
        tmp_path, monkeypatch):
    assert migrations.MIGRATIONS[42] is migrations.m0043_regime_account
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS[:42])
    conn = connect(tmp_path / "o.db")
    assert migrate(conn) == 42
    conn.execute("INSERT INTO strategy_decisions (account, day, symbol, action, reason) "
                 "VALUES ('ai', '2026-09-28', 'AAPL', 'skip', 'x')")
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS[:43])
    assert migrate(conn) == 43
    repo = DecisionRepo(conn)
    [old] = repo.recent("ai")
    assert (old.symbol, old.explanation) == ("AAPL", None)
    conn.close()


def test_a_regime_decision_keeps_its_explanation(conn):
    repo = DecisionRepo(conn)
    why = {"mood": "calm_up", "score": 0.2, "parts": {"momentum": 0.15, "trend": 0.05}}
    repo.log([LoggedDecision("regime", date(2026, 9, 29), "KO", "buy", 100.0, "buy lean", None, explanation=why)])
    [d] = repo.recent("regime")
    assert d.explanation == why and repo.recent("strategy") == []


def test_the_latest_regime_reading_is_the_newest_day_and_a_later_step_that_day_replaces_it(conn):
    states = RegimeStateRepo(conn)
    assert states.latest("regime") is None
    states.save("regime", date(2026, 9, 28), {"mood": "calm_down"}, "2026-09-28T21:00:00Z")
    states.save("regime", date(2026, 9, 29), {"mood": "calm_up"}, "2026-09-29T21:00:00Z")
    states.save("regime", date(2026, 9, 29), {"mood": "volatile_up"}, "2026-09-29T22:00:00Z")
    latest = states.latest("regime")
    assert (latest.day, latest.state, latest.at) == (date(2026, 9, 29), {"mood": "volatile_up"}, "2026-09-29T22:00:00Z")
    assert states.latest("regime-ai") is None


def test_a_database_at_version_43_keeps_its_regime_readings_and_gains_tilts(tmp_path, monkeypatch):
    assert migrations.MIGRATIONS[43] is migrations.m0044_regime_tilts
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS[:43])
    conn = connect(tmp_path / "o.db")
    assert migrate(conn) == 43
    RegimeStateRepo(conn).save("regime", date(2026, 9, 28), {"mood": "calm_up"}, "2026-09-29T15:32:00Z")
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS[:44])
    assert migrate(conn) == 44
    assert RegimeStateRepo(conn).latest("regime").state == {"mood": "calm_up"}
    assert TiltRepo(conn).all("regime-ai") == {}
    conn.close()


def test_each_days_tilt_or_failure_is_kept_per_account_and_a_later_save_that_day_replaces_it(conn):
    tilts = TiltRepo(conn)
    day, before = date(2026, 9, 28), date(2026, 9, 25)
    tilts.save(StoredTilt("regime-ai", before, {}, {"trend": 0.5, "hold": 0.5}, None, "Ollama is not reachable",
                          "default (ollama qwen3:4b)", "2026-09-26T21:00:00Z"))
    kept = StoredTilt("regime-ai", day, {"momentum": 0.6}, {"trend": 0.4, "momentum": 0.6}, "rebound risk", None,
                      "default (ollama qwen3:4b)", "2026-09-29T21:00:00Z")
    tilts.save(kept)
    assert tilts.get("regime-ai", day) == kept and tilts.get("regime", day) is None
    assert set(tilts.all("regime-ai")) == {before, day} and tilts.all("regime-ai")[before].error.startswith("Ollama")
    tilts.save(StoredTilt("regime-ai", day, {"momentum": 1.2}, {}, "again", None, None, "2026-09-29T22:00:00Z"))
    assert tilts.get("regime-ai", day).tilt == {"momentum": 1.2}
