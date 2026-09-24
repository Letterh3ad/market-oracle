from dataclasses import replace
from datetime import datetime, timezone

import pytest

from oracle.config import Config, DiscoveryConfig
from oracle.research.llm import ModelError
from oracle.services import discovery_service
from oracle.services.context import open_context
from oracle.services.links_service import company_links, delete_link
from oracle.services.discovery_service import (
    discover, discovered, gdelt_query, move_discovered, news_query, remove_discovered,
)
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import DEFAULT_LIST, show_watchlist, unwatch, watch
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.store.watchlists import WatchlistRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
CARS = dict(sector="Consumer Discretionary", industry_group="Automobiles & Components")
TESLA_ISIN = "US88160R1014"
UNIVERSE = [
    equity("7203.T", "Toyota Motor Corporation", "Japan", "JPX", currency="JPY", market_cap="Mega Cap", **CARS),
    equity("TSLA", "Tesla, Inc.", isin=TESLA_ISIN, currency="USD", market_cap="Mega Cap", **CARS),
    equity("TL0.DE", "Tesla Inc", "Germany", "GER", isin=TESLA_ISIN, currency="EUR", market_cap="Mega Cap",
           **CARS),
    equity("6902.T", "DENSO Corporation", "Japan", "JPX", currency="JPY", market_cap="Large Cap", **CARS),
    equity("F", "Ford Motor Company", exchange="NYQ", currency="USD", market_cap="Large Cap", **CARS),
    equity("GM", "General Motors Company", exchange="NYQ", currency="USD", market_cap="Large Cap", **CARS),
    equity("7267.T", "Honda Motor Co., Ltd.", "Japan", "JPX", currency="JPY", market_cap="Large Cap", **CARS),
    equity("6752.T", "Panasonic Holdings Corporation", "Japan", "JPX", currency="JPY", market_cap="Large Cap",
           sector="Consumer Discretionary", industry_group="Consumer Durables & Apparel"),
    # A company whose primary line hides the never-listed name another of its lines carries.
    equity("XYZM", "XYZ Motors Inc", isin="US0000000001", currency="USD", market_cap="Large Cap"),
    equity("XYZ.DE", "Tesla XYZ AG", "Germany", "GER", isin="US0000000001", currency="EUR"),
    equity("AAPL", "Apple Inc.", currency="USD", market_cap="Mega Cap"),
]
PRICED = ["7203.T", "TSLA", "6902.T", "F", "GM", "7267.T", "6752.T", "XYZM", "AAPL"]
TOYOTA = "Toyota Motor Corporation"


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for s in PRICED:
        y.add(s, "USD", "America/New_York", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    return y


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def gdelt():
    return FakeGdelt()


@pytest.fixture
def web(yahoo, news, gdelt):
    return FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, news, gdelt)


@pytest.fixture
async def ctx(tmp_path, web):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        watch(c, "7203.T")
        yield c


def silent():
    return ScriptedModel(*[{"companies": []}] * 10)


def headlines(news, name, *titles):
    news.results[news_query(name)] = [
        (f"https://news.test/{i}", t, "Wire", datetime(2026, 9, 23, 8, tzinfo=timezone.utc))
        for i, t in enumerate(titles)]


def settings(ctx, **values):
    ctx.config = replace(ctx.config, discovery=replace(DiscoveryConfig(), **values))


def reasons(run):
    return {s.name: s.reason for s in run.skipped}


def never_tesla(run):
    return any("Tesla" in s.name and "never" in s.reason for s in run.skipped)


async def test_a_run_adds_the_biggest_related_peer_and_never_tesla(ctx):
    run = await discover(ctx, silent())
    assert [(a.symbol, a.relation, a.seed) for a in run.added] == [("6902.T", "peer", "7203.T")]
    assert never_tesla(run)
    [item] = show_watchlist(ctx, DISCOVERED_LIST)
    assert item.symbol == "6902.T" and item.last_close == 11.0, "prices were fetched to confirm them"
    [d] = discovered(ctx)
    assert (d.symbol, d.source, d.seed, d.relation) == ("6902.T", "related", "7203.T", "peer")
    assert d.why == "same industry as 7203.T"


async def test_companies_named_in_the_news_come_first_with_their_headlines(ctx, news):
    settings(ctx, per_tick=2)
    headlines(news, TOYOTA, "Denso to supply Toyota with new chips", "Toyota and Panasonic expand battery venture",
              "Tesla cuts prices, pressure on Toyota")
    model = ScriptedModel({"companies": [
        {"name": "Tesla", "relation": "competitor", "headlines": [3]},
        {"name": "Panasonic", "relation": "partner", "headlines": [2]},
        {"name": "Denso", "relation": "supplier", "headlines": [1]},
    ]})
    run = await discover(ctx, model)
    assert [(a.symbol, a.relation) for a in run.added] == [("6752.T", "partner"), ("6902.T", "supplier")]
    assert "never" in reasons(run)["Tesla"]
    by_symbol = {d.symbol: d for d in discovered(ctx)}
    assert [e.title for e in by_symbol["6752.T"].evidence] == ["Toyota and Panasonic expand battery venture"]
    assert by_symbol["6752.T"].why == "named as a partner of 7203.T in the news"
    [(messages, schema)] = model.calls
    assert "Denso to supply Toyota" in messages[-1]["content"] and "companies" in schema["properties"]


async def test_tesla_is_never_added_even_when_the_setting_leaves_it_out(ctx, news):
    settings(ctx, never=("Nikola",), per_tick=5)
    headlines(news, TOYOTA, "Tesla and Toyota trade blows")
    run = await discover(ctx, ScriptedModel({"companies": [{"name": "Tesla", "relation": "competitor",
                                                            "headlines": [1]}]}))
    assert "TSLA" not in {a.symbol for a in run.added} and "TL0.DE" not in {a.symbol for a in run.added}
    assert "never" in reasons(run)["Tesla"]


async def test_the_never_list_is_checked_on_every_listing_of_the_company(ctx, news):
    headlines(news, TOYOTA, "XYZ Motors signs deal with Toyota")
    run = await discover(ctx, ScriptedModel({"companies": [{"name": "XYZ Motors", "relation": "partner",
                                                            "headlines": [1]}]}))
    assert "never" in reasons(run)["XYZ Motors"]
    assert "XYZM" not in {a.symbol for a in run.added}


async def test_a_removed_company_never_returns(ctx):
    first = await discover(ctx, silent())
    assert remove_discovered(ctx, first.added[0].symbol)
    again = await discover(ctx, silent())
    assert "removed" in reasons(again)["DENSO Corporation"]
    assert [a.symbol for a in again.added] == ["F"]
    unwatch(ctx, "F", DISCOVERED_LIST)  # the Watchlist tab's Remove on the discovered list counts too
    third = await discover(ctx, silent())
    assert "removed" in reasons(third)["Ford Motor Company"] and [a.symbol for a in third.added] == ["GM"]


async def test_watched_or_already_discovered_companies_are_skipped(ctx):
    watch(ctx, "6902.T")
    await discover(ctx, silent())
    run = await discover(ctx, silent())
    assert reasons(run)["DENSO Corporation"] == "already watched"
    assert reasons(run)["Ford Motor Company"] == "already discovered"
    assert [a.symbol for a in run.added] == ["GM"]


async def test_moving_to_my_watchlist_keeps_it_watched_and_off_the_discovered_list(ctx):
    await discover(ctx, silent())
    moved = move_discovered(ctx, "6902.T")
    assert moved.watchlist == DEFAULT_LIST
    assert [i.symbol for i in WatchlistRepo(ctx.conn).items(DISCOVERED_LIST)] == []
    assert "6902.T" in [i.symbol for i in WatchlistRepo(ctx.conn).items(DEFAULT_LIST)]
    run = await discover(ctx, silent())
    assert "6902.T" not in {a.symbol for a in run.added}
    assert [i.symbol for i in WatchlistRepo(ctx.conn).items(DISCOVERED_LIST)] == [a.symbol for a in run.added]


async def test_a_listing_without_recent_prices_is_skipped(ctx, yahoo):
    del yahoo.series["6902.T"]
    yahoo.add("F", "USD", "America/New_York", [bar("2026-09-01", 10.0)])
    run = await discover(ctx, silent())
    assert "no price data" in reasons(run)["DENSO Corporation"]
    assert "out of date" in reasons(run)["Ford Motor Company"]
    assert [a.symbol for a in run.added] == ["GM"]


async def test_a_full_list_adds_nothing_and_fetches_nothing(ctx, web):
    settings(ctx, max_total=1)
    await discover(ctx, silent())
    asked = len(web.requests)
    run = await discover(ctx, silent())
    assert run.added == () and "full" in run.note
    assert len(web.requests) == asked


async def test_nothing_watched_is_nothing_to_start_from(ctx):
    unwatch(ctx, "7203.T")
    run = await discover(ctx, silent())
    assert run.added == () and "watch" in run.note


async def test_failed_sources_and_a_down_model_never_fail_the_run(ctx, news, gdelt):
    news.status = 500
    gdelt.error_text = "Queries must be longer"
    run = await discover(ctx, ScriptedModel(ModelError("Ollama is not reachable")))
    assert [a.symbol for a in run.added] == ["6902.T"]
    assert any("news" in e.lower() for e in run.source_errors)


async def test_a_model_that_is_down_is_not_asked_again_in_the_same_run(ctx, news):
    unwatch(ctx, "7203.T")
    watch(ctx, "AAPL")
    watch(ctx, "GM")
    headlines(news, "Apple Inc.", "Apple and Samsung in chip talks")
    headlines(news, "General Motors Company", "GM and Ford race on trucks")
    model = ScriptedModel(ModelError("Ollama took too long"))
    run = await discover(ctx, model)
    assert len(model.calls) == 1
    assert run.seeds == ("AAPL", "GM") and [a.relation for a in run.added] == ["peer"]
    assert any("model" in e for e in run.source_errors)


async def test_the_model_is_not_asked_when_no_headlines_came_back(ctx):
    model = ScriptedModel()
    await discover(ctx, model)
    assert model.calls == []


async def test_garbled_model_output_adds_nothing_from_the_news(ctx, news):
    headlines(news, TOYOTA, "Toyota and Panasonic expand battery venture")
    run = await discover(ctx, ScriptedModel("Panasonic!!"))
    assert [(a.symbol, a.relation) for a in run.added] == [("6902.T", "peer")]


async def test_a_name_not_in_the_universe_is_skipped_with_a_reason(ctx, news):
    headlines(news, TOYOTA, "Acme Widgets to supply Toyota")
    run = await discover(ctx, ScriptedModel({"companies": [{"name": "Acme Widgets", "relation": "supplier",
                                                            "headlines": [1]}]}))
    assert "not in the universe" in reasons(run)["Acme Widgets"]


async def test_gdelt_headlines_are_read_too(ctx, gdelt):
    gdelt.results[gdelt_query(TOYOTA)] = [
        ("https://g.test/1", "Honda and Toyota race for hybrid buyers", "g.test", datetime(2026, 9, 23, tzinfo=timezone.utc))]
    run = await discover(ctx, ScriptedModel({"companies": [{"name": "Honda", "relation": "competitor",
                                                            "headlines": [1]}]}))
    assert [(a.symbol, a.relation) for a in run.added] == [("7267.T", "competitor")]


async def test_seeds_take_turns_across_runs(ctx):
    settings(ctx, search_movers=False)
    watch(ctx, "GM")
    first = await discover(ctx, silent())
    second = await discover(ctx, silent())
    assert (first.seeds[0], second.seeds[0]) == ("7203.T", "GM")


async def test_only_one_run_at_a_time(ctx, monkeypatch):
    from oracle.services.errors import JobAlreadyRunning
    from oracle.services.jobs_service import single_flight

    with single_flight(ctx, discovery_service.JOB, 60.0):
        with pytest.raises(JobAlreadyRunning):
            await discover(ctx, silent())


async def test_a_moved_company_taken_off_my_list_is_never_rediscovered(ctx):
    await discover(ctx, silent())
    move_discovered(ctx, "6902.T")
    assert unwatch(ctx, "6902.T")
    run = await discover(ctx, silent())
    assert "removed" in reasons(run)["DENSO Corporation"]
    assert "6902.T" not in {a.symbol for a in run.added}


async def test_every_company_the_news_links_to_a_seed_is_kept_as_a_link_with_its_headlines(ctx, news):
    headlines(news, TOYOTA, "Denso to supply Toyota with new chips", "Toyota and Panasonic expand battery venture",
              "Tesla cuts prices, pressure on Toyota")
    run = await discover(ctx, ScriptedModel({"companies": [
        {"name": "Denso", "relation": "supplier", "headlines": [1]},
        {"name": "Panasonic", "relation": "partner", "headlines": [2]},
        {"name": "Tesla", "relation": "competitor", "headlines": [3]},
    ]}))
    assert [a.symbol for a in run.added] == ["6902.T"], "one added, both linked"
    links = {x.symbol: x for x in company_links(ctx, "7203.T")}
    assert set(links) == {"6902.T", "6752.T"}, "never Tesla"
    assert links["6902.T"].relation == "supplier" and links["6902.T"].source == "discovery"
    assert [e.title for e in links["6752.T"].evidence] == ["Toyota and Panasonic expand battery venture"]
    assert company_links(ctx, "6902.T")[0].relation == "customer"


async def test_an_added_peer_is_linked_as_the_same_industry(ctx):
    await discover(ctx, silent())
    [link] = company_links(ctx, "7203.T")
    assert (link.symbol, link.relation, link.evidence) == ("6902.T", "peer", ())


async def test_a_deleted_link_is_not_kept_again_from_the_same_news(ctx, news):
    headlines(news, TOYOTA, "Toyota and Panasonic expand battery venture")
    reply = {"companies": [{"name": "Panasonic", "relation": "partner", "headlines": [1]}]}
    await discover(ctx, ScriptedModel(reply))
    [link] = [x for x in company_links(ctx, "7203.T") if x.symbol == "6752.T"]
    delete_link(ctx, "7203.T", link.id)
    remove_discovered(ctx, "6752.T")
    await discover(ctx, ScriptedModel(reply))
    assert "6752.T" not in {x.symbol for x in company_links(ctx, "7203.T")}
