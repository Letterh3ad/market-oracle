from datetime import datetime, timezone

from oracle.research.laya import LayaScore
from oracle.research.news import Article
from oracle.store import migrations
from oracle.store.articles import ArticleRepo
from oracle.store.db import connect
from oracle.store.laya import LayaRepo
from oracle.store.migrate import migrate

SCORE = LayaScore(0.7, "up", {"up": 0.6, "down": 0.2, "none": 0.2}, "earnings", 0.5)


def test_a_database_at_version_44_keeps_its_articles_and_gains_scores_and_snippets(tmp_path, monkeypatch):
    assert migrations.MIGRATIONS[44] is migrations.m0045_laya_scores
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS[:44])
    conn = connect(tmp_path / "o.db")
    assert migrate(conn) == 44
    conn.execute("INSERT INTO articles (url, title, source, fetched_at) VALUES ('u', 'Old headline', 'google_news', 'x')")
    monkeypatch.setattr("oracle.store.migrate.MIGRATIONS", migrations.MIGRATIONS[:45])
    assert migrate(conn) == 45
    [old] = LayaRepo(conn).unscored("AAPL", "m", [1])
    assert (old.title, old.snippet) == ("Old headline", None)
    conn.close()


def test_a_search_snippet_is_kept_as_first_seen_and_a_pair_is_saved_once_per_model(conn):
    articles = ArticleRepo(conn)
    [a] = articles.save([Article("https://x/1", "Apple beats", "Wire", datetime(2026, 9, 1, tzinfo=timezone.utc),
                                 summary="first")], "ddgs", "t")
    articles.save([Article("https://x/1", "Apple beats", "Wire", None, summary="second")], "ddgs", "t")
    repo = LayaRepo(conn)
    assert repo.unscored("AAPL", "m", [a])[0].snippet == "first"
    repo.save("AAPL", "m", "own", [(a, SCORE)], "t1")
    repo.save("AAPL", "m", "own", [(a, LayaScore(0.1, "down", {}, "other", 0.1))], "t2")
    [kept] = repo.scores("aapl", "m")
    assert (kept.relevant, kept.scored_at, kept.direction_probs) == (0.7, "t1", SCORE.direction_probs)
    assert repo.unscored("aapl", "m", [a]) == [] and len(repo.unscored("AAPL", "other-model", [a])) == 1
