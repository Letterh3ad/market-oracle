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
