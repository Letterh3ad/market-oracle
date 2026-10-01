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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) == 9
    assert [tuple(r) for r in conn.execute("SELECT old, new FROM tb_aliases")] == [("FB", "META")]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"insider_trades", "insider_quarters", "sec_filings", "sec_filings_tried"} <= tables
    conn.execute("INSERT INTO sec_filings VALUES (1, 'a', '4', '', '2020-01-02', NULL)")
    conn.execute("INSERT INTO sec_filings VALUES (2, 'a', '4', '', '2020-01-02', NULL)")
    conn.close()
