from datetime import datetime, timezone

from oracle.research.news import Article
from oracle.store.articles import ArticleRepo
from oracle.store.links import LinkRepo

DAY1 = "2026-09-22T12:00:00Z"
DAY2 = "2026-09-24T12:00:00Z"


def article(conn, n):
    [a] = ArticleRepo(conn).save([Article(f"https://x.test/{n}", f"Headline {n}", "Wire",
                                          datetime(2026, 9, 21, tzinfo=timezone.utc))], "google_news", DAY1)
    return a


def test_a_link_is_kept_with_its_evidence_and_seen_dates_and_grows_on_new_evidence(conn):
    a1, a2 = article(conn, 1), article(conn, 2)
    repo = LinkRepo(conn)
    repo.record("7203.T", "6902.T", "supplier", "discovery", [a1], DAY1)
    repo.record("7203.T", "6902.T", "supplier", "chat", [a1, a2], DAY2)
    [link] = repo.of({"7203.T"})
    assert (link.symbol, link.other, link.relation, link.source) == ("7203.T", "6902.T", "supplier", "discovery")
    assert (link.first_seen, link.last_seen) == (DAY1, DAY2)
    assert [e.id for e in link.evidence] == [a1, a2]
    assert repo.of({"6902.T"}) == [link], "found from either end"


def test_links_come_strongest_first_and_a_cutoff_leaves_out_later_ones(conn):
    a1, a2 = article(conn, 1), article(conn, 2)
    repo = LinkRepo(conn)
    repo.record("7203.T", "F", "competitor", "discovery", [], DAY1)
    repo.record("7203.T", "6902.T", "supplier", "discovery", [a1, a2], DAY2)
    repo.record("7203.T", "GM", "competitor", "discovery", [a1], DAY2)
    assert [x.other for x in repo.of({"7203.T"})] == ["6902.T", "GM", "F"]
    assert [x.other for x in repo.of({"7203.T"}, seen_before="2026-09-23")] == ["F"]


def test_a_deleted_pair_is_hidden_and_never_recorded_again_either_way_round(conn):
    a1 = article(conn, 1)
    repo = LinkRepo(conn)
    repo.record("7203.T", "6902.T", "supplier", "discovery", [a1], DAY1)
    repo.record("6902.T", "7203.T", "customer", "discovery", [a1], DAY1)
    [first, _] = repo.of({"7203.T"})
    repo.delete_pair({"7203.T"}, {"6902.T"}, DAY2)
    assert repo.of({"7203.T"}) == [] and repo.get(first.id).deleted_at == DAY2
    assert repo.deleted({"6902.T", "DNZOY"}, {"7203.T"})
    assert not repo.deleted({"F"}, {"7203.T"})
