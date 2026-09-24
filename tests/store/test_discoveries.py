from datetime import datetime, timezone

from oracle.research.news import Article
from oracle.store.articles import ArticleRepo
from oracle.store.discoveries import DISCOVERED_LIST, DiscoveryRepo
from oracle.store.watchlists import WatchlistRepo

NOW = "2026-09-24T12:00:00Z"


def test_an_addition_joins_the_discovered_list_with_its_reasons_and_evidence(conn):
    [a] = ArticleRepo(conn).save([Article("https://x.test/1", "Ford picks new supplier",
                                          "Reuters", datetime(2026, 9, 23, tzinfo=timezone.utc))], "google_news", NOW)
    repo = DiscoveryRepo(conn)
    repo.add("F", "k-ford", "Ford Motor Company", "related", "7203.T", "competitor", [a], NOW)
    assert [i.symbol for i in WatchlistRepo(conn).items(DISCOVERED_LIST)] == ["F"]
    [d] = repo.on_list()
    assert (d.symbol, d.name, d.source, d.seed, d.relation, d.moved_at) == (
        "F", "Ford Motor Company", "related", "7203.T", "competitor", None)
    assert [(e.id, e.title, e.url) for e in d.evidence] == [(a, "Ford picks new supplier", "https://x.test/1")]


def test_a_listing_put_on_the_list_by_hand_shows_with_no_reasons(conn):
    WatchlistRepo(conn).add(DISCOVERED_LIST, "KO", NOW)
    [d] = DiscoveryRepo(conn).on_list()
    assert (d.symbol, d.source, d.evidence) == ("KO", None, ())


def test_removals_are_remembered_by_symbol_and_company(conn):
    repo = DiscoveryRepo(conn)
    repo.remember_removed("GM", "k-gm", "General Motors", NOW)
    repo.remember_removed("gm", "k-gm", "General Motors", NOW)
    assert repo.removed() == ({"GM"}, {"k-gm"})


def test_a_move_is_recorded_on_the_latest_discovery(conn):
    repo = DiscoveryRepo(conn)
    repo.add("F", "k-ford", "Ford", "related", "7203.T", "peer", [], NOW)
    repo.mark_moved("F", "2026-09-25T00:00:00Z")
    assert repo.latest("F").moved_at == "2026-09-25T00:00:00Z"
    assert repo.latest("GM") is None
