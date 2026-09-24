import pytest

from oracle.store.db import connect
from oracle.store.migrate import SCHEMA_VERSION, SchemaTooNew, current_version, migrate
from oracle.store.migrations import run_ddl


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    return {r[0] for r in rows}


def _migration(ddl):
    class _M:
        @staticmethod
        def up(conn):
            run_ddl(conn, ddl)

    return _M


def test_fresh_database_migrates_to_head(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert current_version(conn) == 0
    assert migrate(conn) == SCHEMA_VERSION
    assert current_version(conn) == SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    before = _tables(conn)
    assert migrate(conn) == SCHEMA_VERSION
    assert _tables(conn) == before


def test_wal_and_foreign_keys_are_on(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_a_failing_migration_leaves_no_partial_schema(tmp_path, monkeypatch):
    class _Broken:
        @staticmethod
        def up(conn):
            conn.execute("CREATE TABLE partial (id INTEGER PRIMARY KEY)")
            raise RuntimeError("boom")

    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", [_Broken])
    conn = connect(tmp_path / "t.db")
    with pytest.raises(RuntimeError, match="boom"):
        migrate(conn)
    assert current_version(conn) == 0
    assert "partial" not in _tables(conn)


def test_a_database_from_a_newer_build_is_refused(conn):
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaTooNew):
        migrate(conn)


def test_ddl_ending_mid_statement_is_rejected(conn):
    with pytest.raises(ValueError):
        run_ddl(conn, "CREATE TABLE ok(a);\nCREATE TABLE broken(a)\n")


def test_semicolon_inside_a_literal_does_not_split_a_statement(conn):
    run_ddl(conn, "CREATE TABLE t(a TEXT DEFAULT 'x;y');\n")
    assert "t" in _tables(conn)


def test_migrating_from_an_earlier_version_does_not_rerun_applied_steps(tmp_path, monkeypatch):
    first = _migration("CREATE TABLE one(a);")
    second = _migration("CREATE TABLE two(a);")
    conn = connect(tmp_path / "t.db")
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", [first])
    migrate(conn)

    def _boom(_conn):
        raise AssertionError("must not re-run an already-applied migration")

    monkeypatch.setattr(first, "up", _boom)
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", [first, second])
    migrate(conn)
    assert current_version(conn) == 2
    assert "two" in _tables(conn)


def test_fx_history_marks_currencies_a_history_source_already_filled(tmp_path, monkeypatch):
    from oracle.store import migrations

    conn = connect(tmp_path / "t.db")
    before = migrations.MIGRATIONS[: migrations.MIGRATIONS.index(migrations.m0012_fx_history)]
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", before)
    migrate(conn)
    conn.executemany(
        "INSERT INTO fx_rates VALUES ('GBP', ?, ?, 1.0, ?, 'x')",
        [("USD", "2026-09-18", "yahoo"), ("USD", "2026-09-22", "open_er_api"), ("TWD", "2026-09-22", "open_er_api")],
    )
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS)
    migrate(conn)
    assert [tuple(r) for r in conn.execute("SELECT currency, filled FROM fx_history")] == [("USD", 1)]


def test_decisions_logged_before_accounts_belong_to_the_strategy(tmp_path, monkeypatch):
    from oracle.store import migrations
    from oracle.store.paper import DecisionRepo

    conn = connect(tmp_path / "t.db")
    before = migrations.MIGRATIONS[: migrations.MIGRATIONS.index(migrations.m0030_decision_accounts)]
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", before)
    migrate(conn)
    conn.execute("INSERT INTO strategy_decisions (day, symbol, action, reason) VALUES ('2026-09-22', 'AAPL', 'skip', 'x')")
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS)
    migrate(conn)
    [old] = DecisionRepo(conn).recent("strategy")
    assert (old.account, old.symbol, old.report_id) == ("strategy", "AAPL", None)
    assert DecisionRepo(conn).recent("ai") == []


def test_ai_decisions_logged_before_the_flag_say_whether_they_need_research(tmp_path, monkeypatch):
    from oracle.store import migrations
    from oracle.store.paper import DecisionRepo

    conn = connect(tmp_path / "t.db")
    before = migrations.MIGRATIONS[: migrations.MIGRATIONS.index(migrations.m0031_decision_research)]
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", before)
    migrate(conn)
    conn.executemany(
        "INSERT INTO strategy_decisions (account, day, symbol, action, reason) VALUES (?, '2026-09-22', ?, 'skip', ?)",
        [("ai", "AAPL", "no verdict yet: run research on it"),
         ("ai", "KO", "the verdict is from 2026-09-10, older than 7 days: run research on it again"),
         ("ai", "MSFT", "no verdict: the verdict was rejected: it cites nothing"),
         ("strategy", "IBM", "no lean yet: analyse it first")],
    )
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS)
    migrate(conn)
    repo = DecisionRepo(conn)
    assert {d.symbol: d.needs_research for d in repo.recent("ai")} == {"AAPL": True, "KO": True, "MSFT": False}
    assert [d.needs_research for d in repo.recent("strategy")] == [False]


def test_a_database_at_version_37_runs_the_replay_study_the_cutoff_probe_then_screened_items(tmp_path, monkeypatch):
    from oracle.store import migrations

    assert migrations.MIGRATIONS[-3:] == [migrations.m0038_replay_study, migrations.m0039_cutoff_probes,
                                          migrations.m0040_screened_items]
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS[:37])
    conn = connect(tmp_path / "o.db")
    assert migrate(conn) == 37
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS)
    assert migrate(conn) == 40 == current_version(conn)
    assert {"cutoff_probes", "cutoff_answers"} <= _tables(conn)
    assert "universe" in {r[1] for r in conn.execute("PRAGMA table_info(discovery_answer_items)")}
    conn.close()
