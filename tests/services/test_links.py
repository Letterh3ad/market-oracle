from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config, DiscoveryConfig
from oracle.research.news import Article
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, UnknownLink
from oracle.services.links_service import LinkFound, company_links, delete_link, record_links, research_links
from oracle.services.universe_service import refresh_universe
from oracle.store.articles import ArticleRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def ctx(tmp_path):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo())
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def articles(ctx, *titles):
    return ArticleRepo(ctx.conn).save([Article(f"https://news.test/{t}", t, "Wire",
                                               datetime(2026, 9, 23, tzinfo=timezone.utc)) for t in titles],
                                      "google_news", "2026-09-23T00:00:00Z")


def test_a_link_reads_from_either_company_with_its_evidence(ctx):
    a, b = articles(ctx, "Apple buys Toyota chips", "Toyota supplies Apple")
    assert record_links(ctx, [LinkFound("AAPL", "7203.T", "supplier", (a, b))], "discovery") == 1
    [from_apple] = company_links(ctx, "AAPL")
    assert (from_apple.symbol, from_apple.name, from_apple.relation) == ("7203.T", "Toyota Motor Corporation",
                                                                         "supplier")
    assert from_apple.why == "a supplier" and [e.id for e in from_apple.evidence] == [a, b]
    [from_toyota] = company_links(ctx, "TM")
    assert (from_toyota.symbol, from_toyota.relation) == ("AAPL", "customer"), "any listing, turned round"


def test_links_to_itself_unknown_listings_or_the_never_list_are_left_out(ctx):
    ctx.config = replace(ctx.config, discovery=replace(DiscoveryConfig(), never=("Coca-Cola",)))
    kept = record_links(ctx, [LinkFound("AAPL", "APC.DE", "peer"), LinkFound("AAPL", "NOPE", "partner"),
                              LinkFound("AAPL", "KO", "partner")], "discovery")
    assert kept == 0 and company_links(ctx, "AAPL") == []


def test_an_unknown_relation_is_refused(ctx):
    with pytest.raises(InvalidQuery):
        record_links(ctx, [LinkFound("AAPL", "KO", "friend")], "chat")


def test_a_deleted_link_stays_deleted_whatever_finds_it_again(ctx):
    [a] = articles(ctx, "Apple and Coca-Cola team up")
    record_links(ctx, [LinkFound("AAPL", "KO", "partner", (a,)), LinkFound("KO", "AAPL", "competitor")], "discovery")
    link = company_links(ctx, "AAPL")[0]
    assert delete_link(ctx, "AAPL", link.id).symbol == "KO"
    assert company_links(ctx, "AAPL") == [] and company_links(ctx, "KO") == []
    assert record_links(ctx, [LinkFound("AAPL.MI", "KO", "partner", (a,)), LinkFound("KO", "AAPL", "peer")],
                        "chat") == 0
    assert company_links(ctx, "AAPL") == []


def test_deleting_a_link_of_another_company_is_refused(ctx):
    record_links(ctx, [LinkFound("AAPL", "KO", "partner")], "discovery")
    [link] = company_links(ctx, "KO")
    with pytest.raises(UnknownLink):
        delete_link(ctx, "T", link.id)


def test_research_takes_the_strongest_linked_companies_with_evidence_known_by_its_day(ctx):
    a, b = articles(ctx, "one", "two")
    record_links(ctx, [LinkFound("AAPL", "T", "customer", (a,)), LinkFound("AAPL", "KO", "partner", (a, b)),
                       LinkFound("KO", "AAPL", "partner", (b,)), LinkFound("AAPL", "7203.T", "peer")], "discovery")
    assert [x.symbol for x in research_links(ctx, "AAPL", 3)] == ["KO", "T"], "one per company, evidence only"
    assert research_links(ctx, "AAPL", 1)[0].symbol == "KO"
    assert research_links(ctx, "AAPL", 3, as_of=NOW.date() - timedelta(days=1)) == []
    assert len(research_links(ctx, "AAPL", 3, as_of=date(2026, 9, 24))) == 2
