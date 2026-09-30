from oracle.store.articles import ArticleRepo
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


def test_a_companys_stored_filings_are_found_without_scanning_every_article(tmp_path):
    # The history backfill stores about 850k articles; the industry panel asks this per company.
    conn = connect(tmp_path / "o.db")
    migrate(conn)
    asked = []
    conn.set_trace_callback(lambda sql: asked.append(sql) if "articles" in sql and "SELECT" in sql else None)
    repo = ArticleRepo(conn)
    repo.latest_about(["AAPL"], 3)
    repo.filings(["AAPL"], "2025-01-01")
    conn.set_trace_callback(None)
    for sql in asked:
        plan = " ".join(r[3] for r in conn.execute(f"EXPLAIN QUERY PLAN {sql}"))
        assert "SCAN a " not in plan + " " and "SCAN articles" not in plan, plan
    conn.close()
