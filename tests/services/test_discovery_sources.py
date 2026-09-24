"""Discovery's theme and mover sources, and how the three sources take turns."""

from datetime import datetime, timezone

from oracle.research.llm import ModelError
from oracle.services.discovery_service import (
    MAX_MOVER_SEARCHES, MAX_THEMES, discover, discovered, mover_query, mover_searches, theme_query,
)
from oracle.services.watch_service import unwatch, watch
from tests.fakes.llm import ScriptedModel
from tests.services.test_discovery import (  # noqa: F401
    ctx, gdelt, news, reasons, settings, silent, web, yahoo,
)

WHEN = datetime(2026, 9, 23, 8, tzinfo=timezone.utc)


def found(news, query, *titles):
    news.results[query] = [(f"https://news.test/{abs(hash((query, t)))}", t, "Wire", WHEN) for t in titles]


def names(*pairs):
    return {"companies": [{"name": n, "headlines": h} for n, h in pairs]}


def only(ctx, source, **values):
    switches = {"search_related": False, "search_themes": False, "search_movers": False}
    settings(ctx, **{**switches, f"search_{source}": True, **values})


async def test_a_theme_is_searched_in_the_news_and_the_companies_named_are_added_with_why(ctx, news):
    only(ctx, "themes", themes=("hybrid cars",))
    found(news, theme_query("hybrid cars"), "Honda bets big on hybrid cars", "Tesla shrugs off hybrid cars")
    model = ScriptedModel(names(("Tesla", [2]), ("Honda", [1])))
    run = await discover(ctx, model)
    assert [(a.symbol, a.source, a.seed) for a in run.added] == [("7267.T", "theme", "hybrid cars")]
    assert "never" in reasons(run)["Tesla"]
    assert run.seeds == ("hybrid cars",)
    [d] = discovered(ctx)
    assert (d.source, d.seed) == ("theme", "hybrid cars")
    assert d.why == 'named in the news on your theme "hybrid cars"'
    assert [e.title for e in d.evidence] == ["Honda bets big on hybrid cars"]
    [(messages, _)] = model.calls
    assert "hybrid cars" in messages[0]["content"] and "Honda bets big" in messages[-1]["content"]


async def test_no_themes_means_the_theme_source_is_skipped(ctx, news):
    only(ctx, "themes", themes=())
    run = await discover(ctx, ScriptedModel())
    assert run.added == () and "Settings" in run.note and news.queries == []


async def test_themes_take_turns_a_few_a_run(ctx, news):
    themes = ("hybrid cars", "batteries", "robots", "chips")
    only(ctx, "themes", themes=themes)
    first = await discover(ctx, silent())
    second = await discover(ctx, silent())
    assert first.seeds == themes[:MAX_THEMES] and second.seeds == themes[MAX_THEMES:2 * MAX_THEMES]


async def test_movers_search_the_regions_the_watchlist_covers_a_few_a_run(ctx, news):
    watch(ctx, "AAPL")
    only(ctx, "movers")
    searches = mover_searches(ctx)
    assert {s.region for s in searches} == {"Japan", "United States"}
    assert {s.kind for s in searches} == {"movers", "volume", "results"}
    await discover(ctx, silent())
    assert 0 < len(news.queries) <= MAX_MOVER_SEARCHES
    await discover(ctx, silent())
    assert len(set(news.queries)) == len(news.queries), "the next run searches the next ones"


async def test_a_company_in_mover_news_is_added_with_the_search_that_found_it(ctx, news):
    only(ctx, "movers")
    search = mover_searches(ctx)[0]
    found(news, mover_query(search), "Panasonic shares jump 12% on battery orders")
    run = await discover(ctx, ScriptedModel(names(("Panasonic", [1]))))
    [a] = run.added
    assert (a.symbol, a.source, a.seed, a.relation) == ("6752.T", "mover", search.label, search.kind)
    [d] = discovered(ctx)
    assert d.why == f"named in news of {search.label}"


async def test_sources_take_turns_across_runs(ctx, news):
    settings(ctx, themes=("hybrid cars",), per_tick=1)
    runs = [await discover(ctx, silent()) for _ in range(4)]
    assert [r.sources[0] for r in runs] == ["related", "themes", "movers", "related"]


async def test_a_failed_source_hands_its_turn_to_the_next(ctx, news, gdelt):
    settings(ctx, themes=("hybrid cars",))
    await discover(ctx, silent())
    news.status = 500
    gdelt.error_text = "Queries must be longer"
    run = await discover(ctx, silent())
    assert run.sources == ("themes", "movers", "related")
    assert any("hybrid cars" in e for e in run.source_errors)
    assert [a.relation for a in run.added] == ["peer"]


async def test_a_switched_off_source_is_never_tried(ctx, news):
    settings(ctx, search_related=False, search_movers=False, themes=("hybrid cars",))
    run = await discover(ctx, silent())
    assert run.sources == ("themes",)
    assert all("hybrid cars" in q for q in news.queries)


async def test_every_source_off_says_so(ctx):
    settings(ctx, search_related=False, search_themes=False, search_movers=False)
    run = await discover(ctx, silent())
    assert run.added == () and "off" in run.note


async def test_themes_run_with_nothing_watched(ctx, news):
    unwatch(ctx, "7203.T")
    settings(ctx, themes=("hybrid cars",))
    found(news, theme_query("hybrid cars"), "Honda bets big on hybrid cars")
    run = await discover(ctx, ScriptedModel(names(("Honda", [1]))))
    assert run.sources == ("themes",) and [a.symbol for a in run.added] == ["7267.T"]


async def test_a_model_down_on_a_theme_is_not_asked_again_for_movers(ctx, news):
    settings(ctx, search_related=False, themes=("hybrid cars",))
    found(news, theme_query("hybrid cars"), "Honda bets big on hybrid cars")
    found(news, mover_query(mover_searches(ctx)[0]), "Panasonic shares jump")
    model = ScriptedModel(ModelError("Ollama is not reachable"))
    run = await discover(ctx, model)
    assert len(model.calls) == 1 and run.added == ()
    assert any("model" in e for e in run.source_errors)
