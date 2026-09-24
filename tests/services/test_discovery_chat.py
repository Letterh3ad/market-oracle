"""Discovery chat: a question answered with a ranked shortlist, refined and rerun."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.research.llm import ModelError
from oracle.research.web import WebSearchFailed
from oracle.services.context import open_context
from oracle.services.discovery_chat_service import (
    add_to_discovered, answer, answer_progress, ask, new_question, question, questions, sweep_interrupted_answers,
)
from oracle.services.discovery_service import discovered, remove_discovered
from oracle.services.discovery_chat_service import _matching
from oracle.services.errors import InvalidQuery, ModelUnavailable, NeverListed, SearchFailed, UnknownQuestion
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.store.questions import QuestionRepo
from oracle.store.watchlists import WatchlistRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages, FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
GRID = "which European companies gain from grid batteries?"
TESLA_ISIN = "US88160R1014"
KIT = dict(sector="Industrials", industry_group="Capital Goods", industry="Electrical Equipment")
UNIVERSE = [
    equity("ENR.DE", "Siemens Energy AG", "Germany", "GER", currency="EUR", market_cap="Large Cap", **KIT),
    equity("FLNC", "Fluence Energy, Inc.", currency="USD", market_cap="Mid Cap", **KIT),
    equity("RWE.DE", "RWE AG", "Germany", "GER", currency="EUR", market_cap="Large Cap", sector="Utilities",
           industry_group="Utilities", industry="Multi-Utilities"),
    equity("TSLA", "Tesla, Inc.", isin=TESLA_ISIN, currency="USD", market_cap="Mega Cap"),
    equity("NIBE-B.ST", "Nibe Industrier AB", "Sweden", "STO", currency="SEK", market_cap="Mid Cap", **KIT),
]
PRICED = ["ENR.DE", "FLNC", "RWE.DE", "TSLA", "NIBE-B.ST"]
HEADLINES = [
    "Siemens Energy wins grid battery order in Germany",
    "Fluence Energy and Siemens Energy team up on grid storage",
    "RWE switches on its biggest grid battery",
    "Tesla Megapacks head to European grid",
]


def plan(*queries, caps=(), exclude=(), local=()):
    return {"queries": list(queries), "local": list(local), "market_caps": list(caps), "exclude": list(exclude)}


def names(*entries):
    """(name, why, items) -> the naming call's reply."""
    return {"companies": [{"name": n, "why": w, "headlines": list(h)} for n, w, h in entries]}


ALL = names(("Siemens Energy", "Wins grid battery orders", [1, 2]), ("Fluence Energy", "Grid storage maker", [2]),
            ("RWE", "Runs grid batteries", [3]), ("Tesla", "Sells Megapacks", [4]))


def model(*replies, planned=None):
    m = ScriptedModel(*replies)
    m.suggestions = planned or plan("grid battery Europe")
    return m


@pytest.fixture
def news():
    n = FakeGoogleNews()
    n.results["grid battery Europe"] = [(f"https://news.test/{i}", t, "Wire", datetime(2026, 9, 23, 20 - i,
                                                                                       tzinfo=timezone.utc))
                                        for i, t in enumerate(HEADLINES)]
    return n


@pytest.fixture
def gdelt():
    return FakeGdelt()


@pytest.fixture
def pages():
    return FakePages()


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for s in PRICED:
        y.add(s, "EUR", "Europe/Berlin", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    return y


@pytest.fixture
def web(yahoo, news, gdelt, pages):
    return FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, news, gdelt, pages)


@pytest.fixture
async def ctx(tmp_path, web):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def listed(view):
    return [(line.item.symbol, line.item.why) for line in view.shortlist]


def skipped(view):
    return {i.name: i.skipped for i in view.skipped}


def naming_call(m):
    return next(c for c in m.calls if "companies" in c[1]["properties"])


async def test_a_question_comes_back_as_a_ranked_shortlist_with_why_and_evidence_never_tesla(ctx, yahoo):
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    assert listed(view) == [("ENR.DE", "Wins grid battery orders"), ("FLNC", "Grid storage maker"),
                            ("RWE.DE", "Runs grid batteries")], "most evidence first"
    assert skipped(view) == {"Tesla": "on the never list"}
    first = view.shortlist[0]
    assert [e.title for e in first.evidence] == HEADLINES[:2]
    assert first.evidence[0].url == "https://news.test/0"
    assert view.answer.status == "done" and view.compared_to is None and not first.new
    assert view.answer.searches[:2] == ("Google News: grid battery Europe", "GDELT: grid battery Europe")
    saved = question(ctx, view.answer.question_id)
    assert saved.question.text == GRID and [a.answer.id for a in saved.answers] == [view.answer.id]


async def test_the_model_is_given_the_question_and_every_item_found(ctx):
    m = model(ALL)
    await ask(ctx, GRID, model=m, searcher=FakeSearcher())
    plan_call = m.calls[0]
    assert plan_call[0][1]["content"] == GRID
    system, user = naming_call(m)[0]
    assert GRID in system["content"]
    assert "[1] 2026-09-23 Wire: Siemens Energy wins grid battery order" in user["content"]


async def test_a_name_the_cited_items_do_not_hold_is_dropped(ctx):
    view = await ask(ctx, GRID, model=model(names(("Vestas", "Wind and storage", [1])), ALL),
                     searcher=FakeSearcher())
    assert view.shortlist == () and view.skipped == ()
    assert "the model named no company the news shows" in view.answer.notes


async def test_a_company_the_user_removed_is_left_off(ctx):
    watch(ctx, "FLNC", DISCOVERED_LIST)
    assert remove_discovered(ctx, "FLNC")
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    assert "FLNC" not in [s for s, _ in listed(view)]
    assert skipped(view)["Fluence Energy"] == "you removed it before"


async def test_a_watched_company_stays_on_marked_and_is_not_priced_again(ctx, web):
    watch(ctx, "ENR.DE")
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    line = view.shortlist[0]
    assert (line.item.symbol, line.watched, line.discovered_as) == ("ENR.DE", True, None)
    priced = {r.url.path.rsplit("/", 1)[-1] for r in web.requests_to("query2.finance.yahoo.com")}
    assert "ENR.DE" not in priced and "FLNC" in priced


async def test_a_listing_without_recent_prices_is_left_off_with_why(ctx, yahoo):
    del yahoo.series["FLNC"]
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    assert "no price data" in skipped(view)["Fluence Energy"]


async def test_a_follow_up_refines_the_question_as_a_new_one_and_marks_what_is_new(ctx):
    first = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    m = model(names(("Fluence Energy", "Grid storage maker", [2]), ("Nibe", "Heat pumps", [1]),
                    ("Siemens Energy", "Wins orders", [1])), planned=plan("grid battery Europe", caps=["Mid Cap"]))
    view = await ask(ctx, "only mid caps", refines=first.answer.question_id, model=m, searcher=FakeSearcher())
    assert m.calls[0][0][1]["content"] == f"{GRID} Follow-up: only mid caps"
    assert listed(view) == [("FLNC", "Grid storage maker")]
    assert skipped(view)["Siemens Energy"] == "not the size asked for (Large Cap)"
    assert "Nibe" not in skipped(view), "not named in the item it cites"
    assert view.compared_to == first.answer.id and not view.shortlist[0].new
    assert {i.symbol for i in view.gone} == {"ENR.DE", "RWE.DE"}
    assert "only mid caps" in view.answer.notes
    q = question(ctx, view.answer.question_id)
    assert [c.text for c in q.chain] == [GRID, "only mid caps"] and q.asked == f"{GRID} Follow-up: only mid caps"
    assert [f.id for f in question(ctx, first.answer.question_id).follow_ups] == [q.question.id]


async def test_excluded_sectors_are_left_off(ctx):
    view = await ask(ctx, "grid batteries, no utilities", searcher=FakeSearcher(),
                     model=model(ALL, planned=plan("grid battery Europe", exclude=["utility"])))
    assert skipped(view)["RWE"] == "in Utilities, which the question leaves out"
    assert "leaving out utility" in view.answer.notes


async def test_an_exclude_the_universe_has_no_sector_for_is_said_not_recognised(ctx):
    view = await ask(ctx, "grid batteries, no crypto", searcher=FakeSearcher(),
                     model=model(ALL, planned=plan("grid battery Europe", exclude=["crypto", "utilities"])))
    assert "leaving out utilities" in view.answer.notes
    assert '"crypto" was not recognised as a sector or industry, so nothing was left out for it' in view.answer.notes


@pytest.mark.parametrize("exclude, fields, found", [
    ("oil and gas", ("Energy", "Energy", "Oil, Gas & Consumable Fuels"), "Oil, Gas & Consumable Fuels"),
    ("gas", ("Utilities", "Utilities", "Gas Utilities"), "Gas Utilities"),
    ("energy sector", ("Energy", "Energy", "Oil, Gas & Consumable Fuels"), "Energy"),
    ("utilities", ("Utilities", "Utilities", "Multi-Utilities"), "Utilities"),
    ("banks", ("Financials", "Banks", "Banks"), "Banks"),
    ("tech", ("Health Care", "Pharmaceuticals, Biotechnology & Life Sciences", "Biotechnology"), None),
    ("the sector", ("Energy", "Energy", "Energy"), None),
    ("consumer staples", ("Consumer Discretionary", "Consumer Discretionary Distribution & Retail",
                          "Broadline Retail"), None),
    ("telecom services", ("Information Technology", "Software & Services", "Software"), None),
    ("medical equipment", ("Information Technology", "Semiconductors & Semiconductor Equipment",
                           "Semiconductor Equipment"), None),
    ("tech", ("Information Technology", "Software & Services", "Software"), "Information Technology"),
    ("pharma", ("Health Care", "Pharmaceuticals, Biotechnology & Life Sciences", "Pharmaceuticals"),
     "Pharmaceuticals, Biotechnology & Life Sciences"),
    ("biotech", ("Health Care", "Health Care Equipment & Services", "Biotechnology"), "Biotechnology"),
    ("insurers", ("Financials", "Insurance", "Insurance"), "Insurance"),
    ("chipmakers", ("Information Technology", "Semiconductors & Semiconductor Equipment", "Semiconductors"),
     "Semiconductors & Semiconductor Equipment"),
])
def test_an_exclude_matches_whole_words_of_a_sector_or_industry(exclude, fields, found):
    assert _matching(exclude, fields) == found


async def test_a_rerun_says_what_is_new_and_what_is_gone(ctx):
    first = await ask(ctx, GRID, model=model(names(("Siemens Energy", "Wins orders", [1]))), searcher=FakeSearcher())
    again = await answer(ctx, first.answer.question_id,
                         model(names(("Fluence Energy", "Grid storage maker", [2]), ("RWE", "Batteries", [3]))),
                         FakeSearcher())
    assert [(line.item.symbol, line.new) for line in again.shortlist] == [("FLNC", True), ("RWE.DE", True)]
    assert [i.symbol for i in again.gone] == ["ENR.DE"] and again.compared_to == first.answer.id
    views = question(ctx, first.answer.question_id).answers
    assert [v.answer.id for v in views] == [again.answer.id, first.answer.id], "newest first"
    assert views[1].compared_to is None


async def test_a_model_that_cannot_answer_fails_the_answer_which_stays_saved(ctx):
    m = model()
    m.suggestions = ModelError("ollama is not running")

    async def down(messages, schema):
        raise ModelError("ollama is not running")

    m.reply = down
    with pytest.raises(ModelUnavailable, match="not running"):
        await ask(ctx, GRID, model=m, searcher=FakeSearcher())
    [line] = questions(ctx)
    assert (line.answers, line.last.status, line.last.error) == (1, "failed", "ollama is not running")


async def test_an_answer_with_no_search_that_ran_fails_and_is_not_compared_against(ctx, news, gdelt):
    first = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    news.status, gdelt.error_text = 503, "down"
    outage = FakeSearcher()
    outage.fail = WebSearchFailed("down")
    with pytest.raises(SearchFailed, match="no search could run"):
        await answer(ctx, first.answer.question_id, model(ALL), outage)
    news.status, gdelt.error_text = 200, None
    after = await answer(ctx, first.answer.question_id, model(ALL), FakeSearcher())
    statuses = [v.answer.status for v in question(ctx, first.answer.question_id).answers]
    assert statuses == ["done", "failed", "done"]
    assert after.compared_to == first.answer.id and after.gone == ()
    assert not any(line.new for line in after.shortlist)


async def test_a_failing_search_is_noted_and_the_answer_goes_on(ctx, gdelt):
    gdelt.error_text = "Your search contained a phrase that was too short"
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    assert len(view.shortlist) == 3
    assert any(n.startswith("could not search GDELT: grid battery Europe") for n in view.answer.notes)


async def test_a_region_is_searched_in_its_own_edition_and_on_the_web(ctx, news):
    searcher = FakeSearcher()
    m = model(ALL, planned=plan("grid battery Europe", local=[{"country": "Germany", "query": "Netzspeicher Aktie"},
                                                              {"country": "Atlantis", "query": "x"}]))
    view = await ask(ctx, GRID, model=m, searcher=searcher)
    assert {"q": "Netzspeicher Aktie", "hl": "de", "gl": "DE", "ceid": "DE:de"} in news.params
    assert searcher.queries == ["grid battery Europe"]
    assert "Google News Germany: Netzspeicher Aktie" in view.answer.searches
    assert not any("Atlantis" in s for s in view.answer.searches)


async def test_a_plan_with_no_queries_searches_the_question_itself(ctx, news):
    await ask(ctx, GRID, model=model(ALL, planned={"nonsense": True}), searcher=FakeSearcher())
    assert GRID.rstrip("?") in news.queries


async def test_articles_are_read_and_their_facts_named_from_apart_from_research_notes(ctx, pages):
    body = "<html><body><p>" + "Siemens Energy booked a 2bn grid storage order. " * 12 + "</p></body></html>"
    pages.pages["https://news.test/0"] = ("text/html", body)
    m = model(ALL)
    m.roles = {"note_taker": {"about_company": True, "facts": ["A 2bn grid storage order"]}}
    await ask(ctx, GRID, model=m, searcher=FakeSearcher())
    assert "  - A 2bn grid storage order" in naming_call(m)[0][1]["content"]
    [(key,)] = ctx.conn.execute("SELECT DISTINCT model FROM article_notes").fetchall()
    assert key == "scripted/script#q1"


async def test_two_questions_never_share_a_note_but_a_follow_up_shares_its_root_s(ctx, pages):
    body = "<html><body><p>" + "Siemens Energy booked a 2bn grid storage order. " * 12 + "</p></body></html>"
    pages.pages["https://news.test/0"] = ("text/html", body)
    notes = []

    def note(messages):
        notes.append(messages[0]["content"])
        return {"about_company": True, "facts": ["A 2bn grid storage order"]}

    def asked(text, refines=None):
        m = model(ALL)
        m.roles = {"note_taker": note}
        return ask(ctx, text, refines=refines, model=m, searcher=FakeSearcher())

    first = await asked(GRID)
    await asked("which companies build grid storage?")
    await asked("only mid caps", refines=first.answer.question_id)
    assert len(notes) == 2, "the second question reads for itself; the follow-up reuses its root's note"
    assert "grid storage?" in notes[1]
    keys = {k for (k,) in ctx.conn.execute("SELECT model FROM article_notes")}
    assert keys == {"scripted/script#q1", "scripted/script#q2"}


async def test_a_name_only_the_outlet_holds_is_dropped(ctx, news):
    news.results["grid battery Europe"] = [("https://news.test/9", "Grid batteries boom in Europe", "RWE Newsroom",
                                            datetime(2026, 9, 23, 8, tzinfo=timezone.utc))]
    view = await ask(ctx, GRID, model=model(names(("RWE", "Runs grid batteries", [1]))), searcher=FakeSearcher())
    assert view.shortlist == () and view.skipped == ()


async def test_the_shortlist_is_numbered_without_gaps_and_left_off_names_come_after(ctx):
    view = await ask(ctx, GRID, searcher=FakeSearcher(), model=model(names(
        ("Tesla", "Megapacks", [4, 1]), ("Siemens Energy", "Orders", [1]), ("RWE", "Batteries", [3]))))
    assert [(line.item.n, line.item.symbol) for line in view.shortlist] == [(1, "ENR.DE"), (2, "RWE.DE")]
    assert [(i.n, i.name) for i in view.skipped] == [(3, "Tesla")]


async def test_progress_shows_the_stage_of_a_running_answer(ctx):
    q = new_question(ctx, GRID)
    seen = []
    m = model(ALL)
    reply = m.reply

    async def spy(messages, schema):
        seen.append(answer_progress(ctx, q.id))
        return await reply(messages, schema)

    m.reply = spy
    await answer(ctx, q.id, m, FakeSearcher())
    assert seen[0] == "planning searches" and "naming companies" in seen
    assert answer_progress(ctx, q.id) is None


async def test_a_blank_question_or_an_unknown_one_to_follow_up_is_refused(ctx):
    with pytest.raises(InvalidQuery):
        new_question(ctx, "   ")
    with pytest.raises(InvalidQuery):
        new_question(ctx, "x" * 301)
    with pytest.raises(UnknownQuestion, match="no question 99"):
        new_question(ctx, "only mid caps", refines=99)


async def test_add_to_discovered_keeps_the_question_as_why_and_refuses_twice_or_skipped(ctx):
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    added = await add_to_discovered(ctx, view.answer.id, 2)
    assert added.symbol == "FLNC" and added.why == f'an answer to your question "{GRID}"'
    [d] = discovered(ctx)
    assert (d.symbol, d.source, d.relation) == ("FLNC", "question", "answer")
    assert [e.title for e in d.evidence] == [HEADLINES[1]]
    with pytest.raises(InvalidQuery, match="already discovered"):
        await add_to_discovered(ctx, view.answer.id, 2)
    tesla = next(i.n for i in view.skipped if i.name == "Tesla")
    with pytest.raises(InvalidQuery):
        await add_to_discovered(ctx, view.answer.id, tesla)
    assert question(ctx, view.answer.question_id).answers[0].shortlist[1].discovered_as == "FLNC"


async def test_a_company_added_from_a_follow_up_quotes_the_whole_question(ctx):
    first = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    follow = await ask(ctx, "only mid caps", refines=first.answer.question_id, searcher=FakeSearcher(),
                       model=model(names(("Fluence Energy", "Grid storage maker", [2]))))
    added = await add_to_discovered(ctx, follow.answer.id, 1)
    assert added.why == f'an answer to your question "{GRID} Follow-up: only mid caps"'


async def test_add_refuses_a_company_removed_never_listed_or_unpriced_since_the_answer(ctx, yahoo):
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    watch(ctx, "ENR.DE", DISCOVERED_LIST)
    assert remove_discovered(ctx, "ENR.DE")
    with pytest.raises(InvalidQuery, match="removed"):
        await add_to_discovered(ctx, view.answer.id, 1)
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, never=("Tesla", "Fluence")))
    with pytest.raises(NeverListed):
        await add_to_discovered(ctx, view.answer.id, 2)
    del yahoo.series["RWE.DE"]
    with pytest.raises(InvalidQuery, match="no price data"):
        await add_to_discovered(ctx, view.answer.id, 3)
    assert WatchlistRepo(ctx.conn).items(DISCOVERED_LIST) == []


async def test_add_to_discovered_respects_the_list_limit(ctx):
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, max_total=0))
    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    with pytest.raises(InvalidQuery, match="full"):
        await add_to_discovered(ctx, view.answer.id, 1)
    assert WatchlistRepo(ctx.conn).items(DISCOVERED_LIST) == []


async def test_an_answer_a_stopped_app_left_running_is_marked_failed(ctx):
    q = new_question(ctx, GRID)
    repo = QuestionRepo(ctx.conn)
    repo.start(q.id, "scripted script", "2026-09-24T10:00:00Z")
    sweep_interrupted_answers(ctx)
    assert repo.answers(q.id)[0].status == "failed"


async def test_a_link_an_answers_news_states_is_kept_with_its_items_never_one_it_does_not(ctx):
    from oracle.services.links_service import company_links

    reply = {"companies": [
        {"name": "Fluence Energy", "why": "Storage with Siemens", "headlines": [2], "related_to": "Siemens Energy",
         "relation": "partner"},
        {"name": "Tesla", "why": "Megapacks", "headlines": [4], "related_to": "Siemens Energy",
         "relation": "competitor"},
        {"name": "RWE", "why": "Runs batteries", "headlines": [3], "related_to": "Fluence Energy",
         "relation": "customer"},
        {"name": "Siemens Energy", "why": "Orders", "headlines": [1]},
    ]}
    view = await ask(ctx, GRID, model=model(reply), searcher=FakeSearcher())
    [link] = company_links(ctx, "ENR.DE")
    assert (link.symbol, link.relations, link.source) == ("FLNC", ("partner",), "chat")
    assert [e.title for e in link.evidence] == [HEADLINES[1]]
    assert company_links(ctx, "RWE.DE") == [], "item 3 never names Fluence"
    assert "kept 1 link between companies the news states (shown on their Company pages)" in view.answer.notes


async def test_an_answer_stating_no_link_keeps_none(ctx):
    from oracle.services.links_service import company_links

    view = await ask(ctx, GRID, model=model(ALL), searcher=FakeSearcher())
    assert all(company_links(ctx, s) == [] for s in ("ENR.DE", "FLNC", "RWE.DE"))
    assert not any("link" in n for n in view.answer.notes)


def with_item(news, title):
    """A fifth item, the oldest, after HEADLINES."""
    news.results["grid battery Europe"].append(("https://news.test/5", title, "Wire",
                                                datetime(2026, 9, 23, 10, tzinfo=timezone.utc)))


RWE_BUYS = {"name": "RWE", "why": "Buys storage", "headlines": [3, 5], "related_to": "Fluence Energy",
            "relation": "customer"}


async def test_a_link_counts_only_its_items_naming_both_and_survives_its_company_being_filtered_off(ctx, news):
    from oracle.services.links_service import company_links

    with_item(news, "RWE buys Fluence Energy batteries for German grid")
    view = await ask(ctx, GRID, model=model({"companies": [RWE_BUYS]}, planned=plan("grid battery Europe",
                                                                                     exclude=["utilities"])),
                     searcher=FakeSearcher())
    assert "RWE" in skipped(view), "the question leaves utilities out"
    [link] = company_links(ctx, "FLNC")
    assert (link.symbol, link.relations) == ("RWE.DE", ("customer",))
    assert [e.title for e in link.evidence] == ["RWE buys Fluence Energy batteries for German grid"]


async def test_a_link_to_a_never_listed_company_is_not_kept(ctx, news):
    from oracle.services.links_service import company_links

    with_item(news, "Tesla and Fluence Energy fight for grid storage deals")
    reply = {"companies": [{"name": "Fluence Energy", "why": "Storage", "headlines": [5], "related_to": "Tesla",
                            "relation": "competitor"}]}
    view = await ask(ctx, GRID, model=model(reply), searcher=FakeSearcher())
    assert company_links(ctx, "FLNC") == [] and not any("link" in n for n in view.answer.notes)


async def test_a_rerun_never_brings_back_a_deleted_chat_link(ctx, news):
    from oracle.services.links_service import company_links, delete_link

    with_item(news, "RWE buys Fluence Energy batteries for German grid")
    view = await ask(ctx, GRID, model=model({"companies": [RWE_BUYS]}), searcher=FakeSearcher())
    [link] = company_links(ctx, "FLNC")
    delete_link(ctx, "FLNC", link.id)
    again = await answer(ctx, view.answer.question_id, model({"companies": [RWE_BUYS]}), FakeSearcher())
    assert company_links(ctx, "FLNC") == [] and not any("link" in n for n in again.answer.notes)
