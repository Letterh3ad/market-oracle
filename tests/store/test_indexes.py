from oracle.store.db import connect
from oracle.store.migrate import migrate


def test_a_symbol_lookup_in_any_case_uses_an_index_not_a_scan(tmp_path):
    # Every symbol a user types is resolved this way; a scan of the full universe
    # (about 150,000 listings) costs around 100ms per lookup.
    conn = connect(tmp_path / "o.db")
    migrate(conn)
    plan = " ".join(r[3] for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT symbol FROM listings WHERE symbol = ? COLLATE NOCASE", ("aapl",)))
    conn.close()
    # "SCAN ... USING COVERING INDEX" still reads every row; only a SEARCH seeks.
    assert plan.startswith("SEARCH") and "SCAN" not in plan
