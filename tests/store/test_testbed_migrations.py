"""The research database's migrations are positional (user_version is the index applied): a
database made before the rename map upgrades in place and keeps its rows."""

import pytest

from oracle.store.db import connect
from oracle.store.migrate import migrate
from oracle.store.testbed_migrations import MIGRATIONS


@pytest.mark.parametrize("at", [6, 7])
def test_a_research_db_from_before_the_rename_map_upgrades_and_keeps_its_rows(tmp_path, at):
    conn = connect(tmp_path / "research.db")
    migrate(conn, MIGRATIONS[:at])
    conn.execute("INSERT INTO tb_members (ticker, bars_note) VALUES ('FB', 'not on Yahoo')")
    conn.execute("INSERT INTO signal_tests (signal, verdict, result, run_at) VALUES ('momentum_12_1', 'FAIL', '{}', 'x')")
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == at
    migrate(conn, MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert conn.execute("SELECT COUNT(*) FROM tb_aliases").fetchone()[0] == 0
    assert [tuple(r) for r in conn.execute("SELECT ticker, bars_note, cik, identity FROM tb_members")] == [
        ("FB", "not on Yahoo", None, None)]
    assert [tuple(r) for r in conn.execute("SELECT signal FROM signal_tests")] == [("momentum_12_1",)]
    conn.close()


def test_a_research_db_with_the_rename_map_gains_the_insider_tables_and_keeps_its_aliases(tmp_path):
    conn = connect(tmp_path / "research.db")
    migrate(conn, MIGRATIONS[:8])
    conn.execute("INSERT INTO tb_aliases (old, new, effective_day, accepted, evidence, verified_at) "
                 "VALUES ('FB', 'META', '2022-06-09', 1, 'sec', 'x')")
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    migrate(conn, MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert [tuple(r) for r in conn.execute("SELECT old, new FROM tb_aliases")] == [("FB", "META")]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"insider_trades", "insider_quarters", "sec_filings", "sec_filings_tried"} <= tables
    conn.execute("INSERT INTO sec_filings VALUES (1, 'a', '4', '', '2020-01-02', NULL)")
    conn.execute("INSERT INTO sec_filings VALUES (2, 'a', '4', '', '2020-01-02', NULL)")
    conn.close()


def test_a_research_db_with_the_insider_tables_gains_fx_and_dividends_for_the_fund_comparison(tmp_path):
    conn = connect(tmp_path / "research.db")
    migrate(conn, MIGRATIONS[:9])
    conn.execute("INSERT INTO signal_tests (signal, verdict, result, run_at) VALUES ('trend_overlay', 'FAIL', '{}', 'x')")
    conn.commit()
    migrate(conn, MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"fx_rates", "fx_history", "dividends", "dividend_spans"} <= tables
    assert "paper_dividends" not in tables, "paper ledgers live in the live database only"
    assert [tuple(r) for r in conn.execute("SELECT signal FROM signal_tests")] == [("trend_overlay",)]
    conn.close()


def test_a_research_db_with_the_registry_gains_the_literature_index_and_keeps_its_rows(tmp_path):
    from oracle.store.testbed_migrations import t0007_lab_registry, t0008_literature
    assert MIGRATIONS.index(t0007_lab_registry) == 10 and MIGRATIONS.index(t0008_literature) == 11
    conn = connect(tmp_path / "research.db")
    migrate(conn, MIGRATIONS[:11])
    conn.execute("INSERT INTO signal_tests (signal, verdict, result, run_at) VALUES ('sue', 'FAIL', '{}', 'x')")
    conn.commit()
    registered = conn.execute("SELECT count(*) FROM lab_tests").fetchone()[0]
    migrate(conn, MIGRATIONS[:12])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
    assert conn.execute("SELECT count(*) FROM lab_tests").fetchone()[0] == registered > 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"lit_sources", "lit_source_ids", "lit_methods", "lit_method_sources", "lit_marks", "lit_searches",
            "lit_search_sources"} <= tables
    assert [tuple(r) for r in conn.execute("SELECT signal FROM signal_tests")] == [("sue",)]
    conn.close()


def test_a_research_db_with_the_literature_index_gains_variants_and_keeps_every_registration_at_one(tmp_path):
    from oracle.store.testbed_migrations import t0009_lab_variants
    assert MIGRATIONS.index(t0009_lab_variants) == 12
    conn = connect(tmp_path / "research.db")
    migrate(conn, MIGRATIONS[:12])
    keys = {r[0] for r in conn.execute("SELECT key FROM lab_tests")}
    migrate(conn, MIGRATIONS[:13])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    assert dict(conn.execute("SELECT key, variants FROM lab_tests").fetchall()) == dict.fromkeys(keys, 1)


def test_meaning_search_adds_method_vectors_and_the_registrations_near_fail_keeping_every_row(tmp_path):
    from oracle.store.testbed_migrations import t0010_meaning
    conn = connect(tmp_path / "research.db")
    migrate(conn, MIGRATIONS[:MIGRATIONS.index(t0010_meaning)])
    registered = conn.execute("SELECT count(*) FROM lab_tests").fetchone()[0]
    migrate(conn, MIGRATIONS)
    assert conn.execute("SELECT count(*) FROM lab_tests WHERE near_fail IS NULL AND near_check IS NULL "
                        "AND near_check_note IS NULL").fetchone()[0] == registered > 0
    assert conn.execute("SELECT count(*) FROM lit_embeddings").fetchone()[0] == 0
    conn.close()


def test_the_funnel_stages_add_the_osap_tables_and_keep_every_method(tmp_path):
    from oracle.store.testbed_migrations import t0011_triage
    conn = connect(tmp_path / "research.db")
    migrate(conn, MIGRATIONS[:MIGRATIONS.index(t0011_triage)])
    conn.execute("INSERT INTO lit_methods (ref, name, status, origin, status_by, status_at, added_at) "
                 "VALUES ('T-01', 'Value', 'go', 'archive', 'seed', 'x', 'x')")
    conn.commit()
    migrate(conn, MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"osap_signals", "osap_returns", "osap_imports", "lab_osap_map", "lab_stages"} <= tables
    assert [r[0] for r in conn.execute("SELECT ref FROM lit_methods")] == ["T-01"]
    conn.close()
