"""Supplier / customer ties read from stored headlines and 10-K filings, typed by the keyword rule
and Laya: kept as links (dated by the document) when both agree, else left unconfirmed for the user."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, LayaConfig
from oracle.research.laya import EdgeType
from oracle.research.news import Article
from oracle.services.activity_service import JobRunner, retry, submit_edges
from oracle.services.context import open_context
from oracle.services.edges_service import (
    CONFIRMED, DISMISSED, LINKED, NONE, SOURCE_FILING, SOURCE_LAYA, UNCONFIRMED, company_edges, confirm_edge, decide,
    dismiss_edge,
    find_edges, find_list_edges,
)
from oracle.services.errors import InvalidQuery, UnknownLink
from oracle.services.industry_service import industry_map
from oracle.services.links_service import LinkFound, add_link, company_links, delete_link, record_links
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.articles import ArticleRepo
from oracle.store.research import ResearchRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.laya import FakeLaya
from tests.fakes.sec import FakeSec
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
AUTOS = dict(sector="Consumer Discretionary", industry_group="Automobiles & Components", industry="Automobiles")
ROWS = [
    equity("7203.T", "Toyota Motor Corporation", "Japan", "JPX", market_cap="Mega Cap", **AUTOS),
    equity("HMC", "Honda Motor Co.", market_cap="Large Cap", **AUTOS),
    equity("6902.T", "DENSO Corporation", "Japan", "JPX", market_cap="Large Cap", **AUTOS),
    equity("BWA", "BorgWarner Inc.", market_cap="Mid Cap", **AUTOS),
    equity("TSLA", "Tesla, Inc.", market_cap="Mega Cap", **AUTOS),
    equity("AAPL", "Apple Inc.", market_cap="Mega Cap", sector="Information Technology"),
]
APPLE_CIK = 320193
ARCHIVE = f"https://www.sec.gov/Archives/edgar/data/{APPLE_CIK}"
TEN_K = f"{ARCHIVE}/000032019325000079/aapl-20250927.htm"
TEN_K_TEXT = ("<html><body><p>We rely on DENSO Corporation for sensors in our products.</p>"
              "<p>Honda Motor is a big rival in cars and we compete hard with it.</p></body></html>")


@pytest.fixture
def sec():
    return FakeSec()


@pytest.fixture
async def ctx(tmp_path, sec):
    web = FakeWeb(FakeFinanceDatabase(ROWS), FakeYahoo(), sec)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        c.config = replace(c.config, research=replace(c.config.research, contact="Faye faye@example.com"))
        yield c


def news(title, day):
    return Article(f"https://news.test/{title}", title, "Wire", datetime(2026, 9, day, 9, tzinfo=timezone.utc))


def shown_to_research(ctx, symbol, *articles):
    ids = ArticleRepo(ctx.conn).save(list(articles), "google_news", "2026-09-29T10:00:00Z")
    repo = ResearchRepo(ctx.conn)
    repo.add_evidence(repo.start(symbol, "ollama", "m", "2026-09-29T10:00:00Z"), ids)
    return ids


def toyota_news(ctx):
    return shown_to_research(ctx, "7203.T", news("Toyota picks Denso as battery supplier", 3),
                             news("Toyota and Honda form joint venture", 5),
                             news("Tesla supplier deal: Tesla to supply Toyota with chips", 6),
                             news("BorgWarner orders parts from Toyota", 7), news("Toyota and Honda shares rise", 8),
                             news("Toyota recalls trucks", 9))


def apple_files(sec, *more):
    sec.companies["AAPL"] = (APPLE_CIK, "Apple Inc.")
    sec.filings[APPLE_CIK] = [("10-K", "2025-10-31", "0000320193-25-000079", "aapl-20250927.htm", "10-K"),
                              ("10-K", "2021-10-29", "0000320193-21-000105", "aapl-20210925.htm", "10-K"), *more]
    sec.documents[TEN_K] = TEN_K_TEXT


def links_of(ctx, symbol):
    return {x.symbol: (x.relations, x.sources, x.since) for x in company_links(ctx, symbol)}


async def test_headlines_naming_two_companies_become_links_when_rule_and_laya_agree_dated_by_the_article(ctx):
    toyota_news(ctx)
    fake = FakeLaya()
    run = await find_edges(ctx, ["7203.T"], typer=fake, filings=False)
    assert (run.documents, run.candidates, run.linked, run.unconfirmed) == (6, 4, 2, 1)
    assert links_of(ctx, "7203.T") == {
        "6902.T": (("supplier",), (SOURCE_LAYA,), ("2026-09-03T09:00:00+00:00",)),
        "HMC": (("partner",), (SOURCE_LAYA,), ("2026-09-05T09:00:00+00:00",)),
    }
    asked = {(i.other, i.kind) for batch in fake.edge_batches for i in batch}
    assert asked == {("DENSO Corporation (6902.T)", "news"), ("Honda Motor Co. (HMC)", "news"),
                     ("BorgWarner Inc. (BWA)", "news")}, "Tesla is never a candidate"
    [pending] = company_edges(ctx, "7203.T", states=(UNCONFIRMED,))
    assert (pending.other, pending.relation, pending.rule, pending.choice) == ("BWA", "customer", "customer", "none")
    assert pending.evidence == "BorgWarner orders parts from Toyota"
    assert "BWA" not in links_of(ctx, "7203.T"), "a low-confidence tie is no link"


async def test_a_link_found_today_is_dated_back_to_an_older_document_showing_it(ctx):
    record_links(ctx, [LinkFound("7203.T", "6902.T", "supplier")], "discovery")
    toyota_news(ctx)
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    assert links_of(ctx, "7203.T")["6902.T"] == (("supplier",), (SOURCE_LAYA,), ("2026-09-03T09:00:00+00:00",))
    [link] = [x for x in company_links(ctx, "7203.T") if x.symbol == "6902.T"]
    assert link.last_seen.startswith("2026-09-30T12"), "last seen stays the latest"


def test_a_tie_only_laya_names_is_no_tie_and_one_only_the_rule_names_waits_for_the_user():
    def typed(choice):
        return EdgeType(choice, 0.9, {})

    assert decide("supplier", typed("supplier")) == ("supplier", LINKED)
    assert decide("supplier", typed("customer")) == ("supplier", UNCONFIRMED)
    assert decide("none", typed("customer")) == ("none", NONE)


async def test_reading_again_types_nothing_twice(ctx):
    toyota_news(ctx)
    fake = FakeLaya()
    await find_edges(ctx, ["7203.T"], typer=fake, filings=False)
    again = await find_edges(ctx, ["7203.T"], typer=fake, filings=False)
    assert (again.documents, again.candidates) == (0, 0) and len(fake.edge_batches) == 1


async def test_a_link_is_left_out_of_the_map_before_its_document_was_known(ctx):
    toyota_news(ctx)
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    linked = lambda day: {r.symbol for r in industry_map(ctx, "7203.T", as_of=day).related if r.link_id}
    assert linked(date(2026, 9, 2)) == set()
    assert linked(date(2026, 9, 3)) == {"6902.T"}
    assert linked(date(2026, 9, 5)) == {"6902.T", "HMC"}


async def test_the_user_confirms_or_deletes_an_unconfirmed_tie(ctx):
    toyota_news(ctx)
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    [pending] = company_edges(ctx, "7203.T", states=(UNCONFIRMED,))
    confirmed = confirm_edge(ctx, "7203.T", pending.id)
    assert confirmed.state == CONFIRMED
    assert links_of(ctx, "7203.T")["BWA"] == (("customer",), (SOURCE_LAYA,), ("2026-09-07T09:00:00+00:00",))
    with pytest.raises(InvalidQuery, match="already"):
        dismiss_edge(ctx, "7203.T", pending.id)
    with pytest.raises(UnknownLink):
        confirm_edge(ctx, "HMC", pending.id + 99)


async def test_a_deleted_tie_stays_out_and_a_deleted_link_is_never_found_again(ctx):
    ids = toyota_news(ctx)
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    [pending] = company_edges(ctx, "7203.T", states=(UNCONFIRMED,))
    assert dismiss_edge(ctx, "7203.T", pending.id).state == DISMISSED
    assert company_edges(ctx, "7203.T", states=(UNCONFIRMED,)) == []
    denso = next(x for x in company_links(ctx, "7203.T") if x.symbol == "6902.T")
    delete_link(ctx, "7203.T", denso.id)
    shown_to_research(ctx, "7203.T", news("Toyota names Denso its main supplier", 20))
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    assert "6902.T" not in links_of(ctx, "7203.T")
    assert ids


async def test_a_us_companys_10ks_since_2022_are_read_once_and_their_ties_dated_by_the_filing(ctx, sec):
    apple_files(sec)
    run = await find_edges(ctx, ["AAPL"], typer=FakeLaya())
    assert (run.filings, run.linked) == (1, 2)
    assert links_of(ctx, "AAPL") == {
        "6902.T": (("supplier",), (SOURCE_FILING,), ("2025-10-31T00:00:00+00:00",)),
        "HMC": (("competitor",), (SOURCE_FILING,), ("2025-10-31T00:00:00+00:00",)),
    }
    [denso] = [e for e in company_edges(ctx, "AAPL") if e.other == "6902.T"]
    assert denso.evidence == "We rely on DENSO Corporation for sensors in our products." and denso.state == LINKED
    fetched = [str(r.url) for r in sec.requests if "/Archives/" in str(r.url)]
    assert fetched == [TEN_K], "the 2021 10-K is older than the history kept"
    await find_edges(ctx, ["AAPL"], typer=FakeLaya())
    assert [str(r.url) for r in sec.requests if "/Archives/" in str(r.url)] == [TEN_K], "never fetched twice"


async def test_a_company_sec_does_not_know_still_has_its_news_read(ctx, sec):
    toyota_news(ctx)
    run = await find_edges(ctx, ["7203.T"], typer=FakeLaya())
    assert run.filings == 0 and run.linked == 2 and not run.errors


async def test_the_main_list_runs_every_company_on_it_with_one_model(ctx, sec):
    toyota_news(ctx)
    apple_files(sec)
    watch(ctx, "7203.T")
    watch(ctx, "AAPL")
    fake = FakeLaya()
    run = await find_list_edges(ctx, "main", typer=fake)
    assert run.symbols == ("7203.T", "AAPL") and run.linked == 4 and not fake.closed, "a caller's model stays open"


async def test_tesla_is_never_read_for_ties(ctx):
    with pytest.raises(InvalidQuery, match="never"):
        await find_edges(ctx, ["TSLA"], typer=FakeLaya())


async def test_laya_off_refuses_the_job(ctx):
    ctx.config = replace(ctx.config, laya=LayaConfig(enabled=False))
    with pytest.raises(InvalidQuery, match="Laya is off"):
        await find_edges(ctx, ["7203.T"], typer=FakeLaya())


async def test_the_job_finds_a_companys_ties_and_can_be_retried(ctx, monkeypatch):
    toyota_news(ctx)
    monkeypatch.setattr("oracle.services.edges_service.load_scorer", lambda c: FakeLaya())
    runner = JobRunner(ctx)
    job = submit_edges(ctx, runner, "7203.T")
    await job.finished.wait()
    assert job.state == "done" and job.result.linked == 2 and job.kind == "edges"
    empty = submit_edges(ctx, runner, None, watchlist="main")
    await empty.finished.wait()
    assert empty.state == "failed" and "empty" in empty.error
    watch(ctx, "7203.T")
    again = retry(ctx, runner, empty.id)
    await again.finished.wait()
    assert again.state == "done" and again.label == "Find ties for list main" and again.result.symbols == ("7203.T",)


def test_a_sentence_listing_four_or_more_companies_is_never_linked_by_itself():
    agreed = EdgeType("competitor", 0.9, {})
    assert decide("competitor", agreed, names=3) == ("competitor", LINKED)
    assert decide("competitor", agreed, names=4) == ("competitor", UNCONFIRMED)


async def test_a_10k_list_of_competitors_waits_for_the_user(ctx, sec):
    apple_files(sec)
    sec.documents[TEN_K] = ("<html><body><p>Our competitors, and rivals, include Honda Motor, DENSO Corporation, "
                            "BorgWarner and Toyota Motor Corporation in cars.</p></body></html>")
    run = await find_edges(ctx, ["AAPL"], typer=FakeLaya())
    assert (run.linked, run.unconfirmed) == (0, 4) and links_of(ctx, "AAPL") == {}
    assert {e.relation for e in company_edges(ctx, "AAPL")} == {"competitor"}


async def test_a_tie_is_one_row_however_many_documents_show_it(ctx):
    shown_to_research(ctx, "7203.T", news("Toyota and BorgWarner partner on axles", 7),
                      news("Toyota and BorgWarner partner on axles again", 12))
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    [tie] = company_edges(ctx, "7203.T")
    assert (tie.other, tie.relation, tie.state, tie.documents, tie.known_at[:10]) == (
        "BWA", "partner", UNCONFIRMED, 2, "2026-09-07")


async def test_an_unconfirmed_tie_already_linked_by_another_document_shows_as_linked(ctx):
    shown_to_research(ctx, "7203.T", news("Toyota and BorgWarner partner on axles", 7),
                      news("Toyota and BorgWarner form joint venture", 12))
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    [tie] = company_edges(ctx, "7203.T")
    assert (tie.state, tie.documents, tie.known_at[:10]) == (LINKED, 2, "2026-09-07")
    with pytest.raises(InvalidQuery, match="already linked"):
        dismiss_edge(ctx, "7203.T", tie.id)


async def test_a_deleted_tie_is_remembered_so_a_later_document_never_links_it(ctx):
    shown_to_research(ctx, "7203.T", news("Toyota and BorgWarner partner on axles", 7),
                      news("Toyota and BorgWarner partner on axles again", 12))
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    [tie] = company_edges(ctx, "7203.T")
    dismiss_edge(ctx, "7203.T", tie.id)
    assert company_edges(ctx, "7203.T") == []
    shown_to_research(ctx, "7203.T", news("Toyota and BorgWarner form joint venture", 20))
    run = await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    assert run.linked == 0 and "BWA" not in links_of(ctx, "7203.T") and company_edges(ctx, "7203.T") == []


async def test_a_tie_whose_link_the_user_deleted_on_the_map_is_no_longer_shown(ctx):
    toyota_news(ctx)
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    denso = next(x for x in company_links(ctx, "7203.T") if x.symbol == "6902.T")
    delete_link(ctx, "7203.T", denso.id)
    assert "6902.T" not in {e.other for e in company_edges(ctx, "7203.T")}


async def test_an_older_document_dates_a_users_link_back_but_keeps_it_theirs(ctx):
    add_link(ctx, "7203.T", "6902.T", "supplier")
    toyota_news(ctx)
    await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    assert links_of(ctx, "7203.T")["6902.T"] == (("supplier",), ("user",), ("2026-09-03T09:00:00+00:00",))


async def test_a_cancel_while_waiting_for_model_work_never_loads_laya_and_keeps_the_documents_unread(ctx, monkeypatch):
    toyota_news(ctx)
    monkeypatch.setattr("oracle.services.edges_service.load_scorer", lambda c: pytest.fail("Laya was loaded"))
    monkeypatch.setattr("oracle.services.edges_service.wait", lambda s: None)
    asked: list[int] = []

    def busy():
        asked.append(1)
        return "Research AAPL"

    run = await find_edges(ctx, ["7203.T"], filings=False, busy=busy, stop=lambda: len(asked) >= 2)
    assert run.stopped and run.documents == 0 and company_edges(ctx, "7203.T") == []
    again = await find_edges(ctx, ["7203.T"], typer=FakeLaya(), filings=False)
    assert again.documents == 6
