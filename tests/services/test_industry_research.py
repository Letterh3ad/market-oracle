"""Daily industry research: new headlines of the daily set and their industry maps, scored with
Laya per (article, target) pair under the map's relation, with budgets, yielding and features."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import AnalystsConfig, Config
from oracle.research.laya import MODEL_TAG
from oracle.services import industry_research_service, tick_service
from oracle.services.activity_service import JobRunner, retry, submit_industry
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.history_service import search_for
from oracle.services.industry_research_service import (
    daily_targets, industry_news, relation_for_kinds, research_industry,
)
from oracle.services.laya_features import stored_features
from oracle.services.laya_service import SharedScorer, laya_scores
from oracle.services.links_service import add_link
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity, etf
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.laya import FakeLaya
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
SINCE = "2026-09-27"
AUTOS = dict(sector="Consumer Discretionary", industry_group="Automobiles & Components")


def car(symbol, name, cap, industry="Automobiles", **extra):
    return equity(symbol, name, market_cap=cap, **{"currency": "USD", "industry": industry, **AUTOS, **extra})


ROWS = [
    car("HMC", "Honda Motor Co.", "Large Cap"),
    car("GM", "General Motors", "Large Cap"),
    car("TSLA", "Tesla, Inc.", "Mega Cap"),
    car("BWA", "BorgWarner", "Mid Cap", industry="Auto Components"),
]


@pytest.fixture
async def env(tmp_path):
    news = FakeGoogleNews()
    web = FakeWeb(FakeFinanceDatabase(ROWS, [etf("CARZ", "Global Auto ETF")]), FakeYahoo(), news)
    manager = open_context(Config(db_path=tmp_path / "o.db", analysts=AnalystsConfig(peers=5)), None, FakeClock(NOW),
                           web.transport)
    ctx = manager.__enter__()
    await refresh_universe(ctx)
    watch(ctx, "HMC")
    add_link(ctx, "HMC", "BWA", "supplier")
    yield ctx, news
    manager.__exit__(None, None, None)


def q(ctx, symbol, name):
    return f"{search_for(ctx, symbol, name).query} after:{SINCE}"


def when(day, hour=9):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


def give(ctx, news, symbol, name, *items):
    news.results[q(ctx, symbol, name)] = [(f"https://n.test/{t}", t, "Wire", d) for t, d in items]


def run(ctx, fake=None, **kw):
    return research_industry(ctx, scorer=fake or FakeLaya(), **kw)


def by_title(ctx, symbol="HMC", model="fake-laya"):
    return {s.title: s for s in laya_scores(ctx, symbol, model=model)}


@pytest.mark.parametrize("kinds, relation", [
    (("competitor",), "competitor"), (("peer",), "competitor"), (("supplier", "competitor"), "supplier"),
    (("customer",), "customer"), (("partner",), "industry"), (("other", "competitor"), "competitor"),
])
def test_a_map_relation_gives_the_feature_its_news_counts_in(kinds, relation):
    assert relation_for_kinds(kinds) == relation


async def test_each_company_and_its_map_are_searched_once_and_scored_per_pair_under_the_maps_relation(env):
    ctx, news = env
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda profits rise", when(29)))
    give(ctx, news, "GM", "General Motors", ("GM recall of trucks", when(28)))
    give(ctx, news, "BWA", "BorgWarner", ("BorgWarner beats estimates", when(29)))
    r = await run(ctx)
    assert r.targets == ("HMC",) and r.reached == 1 and r.searched == 3 and r.requests == 3
    assert sorted(news.queries) == sorted([q(ctx, "HMC", "Honda Motor Co."), q(ctx, "GM", "General Motors"),
                                           q(ctx, "BWA", "BorgWarner")]), "never Tesla, never the fund"
    scores = by_title(ctx)
    assert {t: s.relation for t, s in scores.items()} == {
        "Honda profits rise": "own", "GM recall of trucks": "competitor", "BorgWarner beats estimates": "supplier"}
    assert r.scored == 3 and r.feature_rows == 3
    rows = stored_features(ctx, "HMC", date(2026, 9, 27), date(2026, 9, 30), model="fake-laya")
    assert [(x.day.day, x.relation, x.score) for x in rows] == [(28, "competitor", -0.8), (29, "own", 0.8),
                                                                 (29, "supplier", 0.8)]


async def test_undated_and_older_results_are_dropped_never_dated_by_the_fetch_day(env):
    ctx, news = env
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda in window", when(27, 0)), ("Honda undated", None),
         ("Honda old", when(26, 23)))
    r = await run(ctx)
    assert (r.kept, r.undated, r.outside) == (1, 1, 1)
    assert list(by_title(ctx)) == ["Honda in window"]


async def test_a_second_run_soon_after_searches_nothing_and_scores_nothing_twice(env):
    ctx, news = env
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda profits rise", when(29)))
    await run(ctx)
    asked = len(news.queries)
    again = await run(ctx)
    assert len(news.queries) == asked and again.searched == 0
    assert (again.scored, again.already) == (0, 1)
    ctx.clock.advance(13 * 3600)
    await run(ctx)
    assert len(news.queries) == 2 * asked, "stale after twelve hours"


async def test_the_request_budget_stops_searching_but_what_is_stored_is_still_scored(env):
    ctx, news = env
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda profits rise", when(29)))
    r = await run(ctx, max_requests=1)
    assert r.requests == 1 and r.searches_stopped == "request budget (1)"
    assert r.reached == 1 and r.scored == 1 and r.stopped is None


async def test_the_time_budget_ends_the_run_and_keeps_what_it_finished(env):
    ctx, news = env
    r = await run(ctx, minutes=0)
    assert r.stopped == "time budget (0 min)" and r.reached == 0 and r.requests == 0
    assert "stopped: time budget" in r.summary


async def test_cancel_stops_between_searches(env):
    ctx, news = env
    r = await run(ctx, stop=lambda: len(news.queries) >= 1)
    assert r.stopped == "cancelled" and len(news.queries) == 1


async def test_live_model_work_is_waited_out_between_searches(env):
    ctx, news = env
    calls = []

    def busy():
        calls.append(1)
        return "Research GM" if len(calls) <= 2 else None

    await run(ctx, busy=busy)
    assert 5.0 in ctx.clock.sleeps


async def test_a_shared_model_is_closed_while_live_work_runs_and_loaded_again_after(env, laya_waits):
    ctx, news = env
    watch(ctx, "GM")
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda profits rise", when(29)))
    give(ctx, news, "GM", "General Motors", ("GM recall of trucks", when(28)))
    models = []

    def load():
        models.append(FakeLaya())
        return models[-1]

    shared = SharedScorer(load, "fake-laya")
    fired = []

    def busy():
        if shared.loaded and not fired:
            fired.append(1)
            return "Research AAPL"
        return None

    r = await research_industry(ctx, scorer=shared, busy=busy)
    assert r.reached == 2 and shared.loads == 2 and models[0].closed, "closed while research ran, then reloaded"
    assert laya_waits and not shared.loaded, "closed at the end"


async def test_tesla_and_funds_are_never_researched(env):
    ctx, _ = env
    for symbol in ("TSLA", "CARZ"):
        with pytest.raises(InvalidQuery):
            await run(ctx, symbols=[symbol])


async def test_laya_off_refuses(env):
    ctx, _ = env
    ctx.config = replace(ctx.config, laya=replace(ctx.config.laya, enabled=False))
    with pytest.raises(InvalidQuery, match="Laya is off"):
        await run(ctx)


async def test_a_run_that_cannot_load_laya_stops_and_says_why(env):
    ctx, news = env
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda profits rise", when(29)))

    def broken():
        raise InvalidQuery("laya or torch will not load")

    r = await research_industry(ctx, scorer=SharedScorer(broken, MODEL_TAG))
    assert r.stopped.startswith("Laya: laya or torch will not load") and r.reached == 0


def test_the_daily_set_is_the_main_list_then_other_lists_then_discovered_never_tesla(env):
    ctx, _ = env
    watch(ctx, "GM", "cars")
    watch(ctx, "BWA", "discovered")
    assert daily_targets(ctx) == ["HMC", "GM", "BWA"]


async def test_the_panel_shows_each_related_companys_latest_events_and_what_they_moved_today(env):
    ctx, news = env
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda profits rise", when(29)))
    give(ctx, news, "GM", "General Motors", ("GM recall of trucks", when(30, 8)), ("GM opens plant", when(28)))
    give(ctx, news, "BWA", "BorgWarner", ("BorgWarner beats estimates", when(29)))
    await run(ctx)
    view = industry_news(ctx, "HMC", model="fake-laya")
    assert view.day == date(2026, 9, 30)
    assert [(f.relation, f.score) for f in view.features] == [("competitor", -0.8)]
    [gm, bwa] = view.related
    assert (gm.symbol, gm.relation, gm.day_articles, gm.day_score) == ("GM", "competitor", 1, -0.8)
    assert [e.title for e in gm.events] == ["GM recall of trucks", "GM opens plant"]
    assert (bwa.symbol, bwa.relation, bwa.day_articles) == ("BWA", "supplier", 0)
    assert [e.title for e in view.own.events] == ["Honda profits rise"] and view.last_searched is not None


async def test_the_tick_stage_never_fails_the_tick_and_says_when_it_is_off(env, monkeypatch):
    ctx, _ = env
    monkeypatch.setattr(tick_service, "research_industry", research_industry)

    async def boom(*a, **kw):
        raise RuntimeError("feed parser exploded")

    ctx.config = replace(ctx.config, laya=replace(ctx.config.laya, industry_daily=False))
    assert (await tick_service._industry(ctx, lambda _: None))[0].startswith("industry research is off")
    ctx.config = replace(ctx.config, laya=replace(ctx.config.laya, industry_daily=True))
    monkeypatch.setattr(tick_service, "research_industry", boom)
    stage = await tick_service._run_stage(ctx, "industry", tick_service._industry, lambda _: None)
    assert stage.state == "failed" and "exploded" in stage.error


async def test_the_tick_stage_runs_the_daily_set_and_gives_way_to_a_research_lock(env, monkeypatch):
    ctx, news = env
    seen = {}

    async def spy(c, **kw):
        seen.update(kw)
        return industry_research_service.IndustryRun(("HMC",), reached=1)

    monkeypatch.setattr(tick_service, "research_industry", spy)
    summary, error = await tick_service._industry(ctx, lambda _: None)
    assert summary.startswith("1 of 1 companies") and error is None
    assert seen["busy"]() is None
    from oracle.services.jobs_service import single_flight

    with single_flight(ctx, "research", 60):
        assert seen["busy"]() == "research"


async def test_the_button_is_one_activity_job_that_can_be_retried(env, monkeypatch):
    ctx, news = env
    give(ctx, news, "HMC", "Honda Motor Co.", ("Honda profits rise", when(29)))
    monkeypatch.setattr("oracle.services.laya_service.load_scorer", lambda c: FakeLaya())
    runner = JobRunner(ctx)
    job = submit_industry(ctx, runner, "HMC")
    assert submit_industry(ctx, runner, "HMC") is job, "pressed twice joins the first"
    done = await runner.wait(job.id)
    assert (done.state, done.kind, done.result.reached) == ("done", "industry", 1)
    cancelled = submit_industry(ctx, runner)
    runner.cancel(cancelled.id)
    again = retry(ctx, runner, cancelled.id)
    assert (again.kind, again.subject) == ("industry", "daily") and again.id != cancelled.id
    await runner.wait(again.id)
