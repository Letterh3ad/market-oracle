"""The reading stage: after the loop, the run reads the articles it was shown and notes facts."""

from datetime import date, datetime, timezone

import pytest

from oracle.research.llm import ModelError
from oracle.research.trace import NOT_ABOUT, NOTHING, Evidence
from oracle.research.web import WebResult
from oracle.services.research_service import latest_research, research, research_progress
from tests.fakes.llm import ScriptedModel, step
from tests.services.test_research import ctx, gdelt, news, pages, searcher, sec, utc  # noqa: F401

BODY = "<html><body><p>" + "Apple said revenue rose 8% to $95bn. " * 12 + "</p></body></html>"
FACTS = {"about_company": True, "facts": ["Revenue rose 8% to $95bn"]}


def story(n: int, outlet: str, day: str, host: str | None = None) -> tuple:
    return (f"https://{host or outlet.lower()}.test/{n}", f"Story {n}", outlet, utc(day))


def model_reading(**roles) -> ScriptedModel:
    model = ScriptedModel(step("news_search", "Apple"), step("final", "Done [a1]."))
    model.roles = {"note_taker": FACTS, **roles}
    return model


def note_calls(model) -> list:
    return [c for c in model.calls if c[0][0]["content"].startswith("Role: note_taker.")]


def role_input(model, role: str) -> str:
    [call] = [c for c in model.calls if c[0][0]["content"].startswith(f"Role: {role}.")]
    return call[0][1]["content"]


def fetched(pages) -> list[str]:
    return [str(r.url) for r in pages.requests]


def by_id(run, article_id: int) -> Evidence:
    return next(e for e in run.evidence if e.id == article_id)


async def test_articles_are_read_newest_first_one_per_outlet_before_repeats(ctx, news, pages, monkeypatch):
    import oracle.services.research_service as service

    monkeypatch.setattr(service, "READ_ARTICLES", 3)
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20"), story(2, "Reuters", "2026-09-19"),
                             story(3, "BBC", "2026-09-18"), story(4, "FT", "2026-09-17")]
    for url, *_ in news.results["Apple"]:
        pages.pages[url] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading())
    assert fetched(pages) == ["https://reuters.test/1", "https://bbc.test/3", "https://ft.test/4"]
    assert [e.id for e in run.evidence if e.facts] == [1, 3, 4]
    assert by_id(run, 2).facts == () and by_id(run, 2).read is None


async def test_the_facts_sit_under_their_evidence_line_for_the_roles_and_the_verdict(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20"), story(2, "BBC", "2026-09-19")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    model = model_reading(news_analyst={"findings": [{"text": "Revenue up", "cites": ["a1"]}]})
    run = await research(ctx, "AAPL", model)
    entry = "[a1] 2026-09-20 Reuters: Story 1\n  - Revenue rose 8% to $95bn [a1]"
    assert entry in role_input(model, "news_analyst") and entry in role_input(model, "verdict")
    assert "[a2] 2026-09-19 BBC: Story 2" in role_input(model, "news_analyst")
    assert run.report.findings[0].cites == (1,)
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",) and latest_research(ctx, "AAPL").evidence == run.evidence
    [(system, user)] = [c[0] for c in note_calls(model)]
    assert "Apple Inc. (AAPL)" in system["content"] and "Apple said revenue rose 8%" in user["content"]
    (text,) = ctx.conn.execute("SELECT text FROM article_texts WHERE article_id = 1").fetchone()
    assert text.startswith("Apple said revenue rose 8%")


@pytest.mark.parametrize("page, why", [
    (("status", "403"), "the page answered HTTP 403"),
    (("text/html", "<html><body><div id='app'></div><script>x()</script></body></html>"),
     "too little text to read (paywalled or built by scripts)"),
    (("application/pdf", "%PDF"), "not a web page (application/pdf)"),
    (("timeout", ""), "the page unavailable"),
])
async def test_an_article_that_cannot_be_read_is_skipped_with_why_and_the_run_goes_on(ctx, news, pages, page, why):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = page
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    assert run.status == "done" and run.report is not None and note_calls(model) == []
    assert by_id(run, 1).read.startswith(why) and by_id(run, 1).facts == ()
    assert "try another tool" not in by_id(run, 1).read
    assert "[a1] 2026-09-20 Reuters: Story 1\n" in role_input(model, "verdict") + "\n"


async def test_a_second_run_reuses_the_notes_without_fetching_or_asking_again(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20"), story(2, "BBC", "2026-09-19")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    pages.pages["https://bbc.test/2"] = ("status", "403")
    await research(ctx, "AAPL", model_reading())
    requests = len(pages.requests)
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    assert len(pages.requests) == requests and note_calls(model) == []
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)
    assert by_id(run, 2).read == "the page answered HTTP 403"
    assert "Revenue rose 8% to $95bn [a1]" in role_input(model, "verdict")


async def test_a_passing_failure_is_tried_again_by_the_next_run(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("timeout", "")
    await research(ctx, "AAPL", model_reading())
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading())
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)


async def test_another_model_notes_the_article_itself_but_does_not_fetch_it_again(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    await research(ctx, "AAPL", model_reading())
    requests = len(pages.requests)
    model = model_reading(note_taker={"about_company": True, "facts": ["Guidance raised"]})
    model.model = "other"
    run = await research(ctx, "AAPL", model)
    assert len(pages.requests) == requests and len(note_calls(model)) == 1
    assert by_id(run, 1).facts == ("Guidance raised",)


async def test_a_garbled_note_gives_no_facts_and_is_asked_again_next_time(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(note_taker="garbled"))
    assert run.status == "done" and by_id(run, 1).read == "the model's note was unusable"
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    assert len(note_calls(model)) == 1 and by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)


async def test_a_backend_failing_on_one_note_costs_only_that_note(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(note_taker=ModelError("ollama timed out")))
    assert run.status == "done" and by_id(run, 1).read == "the model could not note it: ollama timed out"


async def test_an_article_not_about_the_company_says_so_and_brings_no_facts(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20"), story(2, "BBC", "2026-09-19")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    pages.pages["https://bbc.test/2"] = ("text/html", BODY)

    def note(messages):
        about = "Story 1" in messages[1]["content"]
        return {"about_company": about, "facts": ["Pears are up"] if not about else []}

    model = model_reading(note_taker=note)
    run = await research(ctx, "AAPL", model)
    assert (by_id(run, 1).facts, by_id(run, 1).read) == ((), NOTHING)
    assert (by_id(run, 2).facts, by_id(run, 2).read) == ((), NOT_ABOUT)
    assert f"[a2] 2026-09-19 BBC: Story 2 (read: {NOT_ABOUT})" in role_input(model, "verdict")
    assert "Pears" not in role_input(model, "verdict")


GOOGLE = "https://news.google.com/rss/articles/CBMiabc"


async def test_a_google_news_link_is_resolved_by_its_headline_at_the_outlets_own_site(ctx, news, pages, searcher):
    news.results["Apple"] = [(GOOGLE, "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    searcher.results["Apple beats estimates"] = [
        WebResult("https://copycat.test/apple", "Apple beats estimates", ""),
        WebResult("https://www.reuters.com/markets/apple-beats", "Apple beats estimates | Reuters", "")]
    pages.pages["https://www.reuters.com/markets/apple-beats"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert fetched(pages) == ["https://www.reuters.com/markets/apple-beats"]
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",) and by_id(run, 1).url == GOOGLE
    (url,) = ctx.conn.execute("SELECT url FROM article_texts WHERE article_id = 1").fetchone()
    assert url == "https://www.reuters.com/markets/apple-beats"


async def test_a_google_news_link_with_no_match_at_the_outlet_is_skipped_with_why(ctx, news, pages, searcher):
    news.results["Apple"] = [(GOOGLE, "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    searcher.results["Apple beats estimates"] = [WebResult("https://copycat.test/apple", "Apple beats", "")]
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert pages.requests == []
    assert by_id(run, 1).read == "could not resolve the Google News link: no search result from reuters.com"


async def test_a_replay_reads_dated_articles_but_leaves_google_news_links_unresolved(ctx, news, pages, searcher):
    news.results["Apple before:2026-09-20"] = [(GOOGLE, "Apple beats estimates", "Reuters", utc("2026-09-19")),
                                               story(2, "BBC", "2026-09-18")]
    pages.pages["https://bbc.test/2"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), as_of=date(2026, 9, 19), searcher=searcher)
    assert searcher.queries == [] and fetched(pages) == ["https://bbc.test/2"]
    assert by_id(run, 1).read.startswith("not resolved in a replay")
    assert by_id(run, 2).facts == ("Revenue rose 8% to $95bn",)


async def test_a_page_the_loop_read_is_noted_without_a_second_fetch(ctx, pages):
    pages.pages["https://news.test/story"] = ("text/html", BODY)
    model = ScriptedModel(step("read_page", "https://news.test/story"), step("final", "Done [a1]."))
    model.roles = {"note_taker": FACTS}
    run = await research(ctx, "AAPL", model)
    assert len(pages.requests) == 1 and by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)


async def test_a_thin_page_the_loop_read_is_marked_unreadable_without_a_second_fetch(ctx, pages):
    pages.pages["https://news.test/story"] = ("text/html", "<p>Sign in to read.</p>")
    model = ScriptedModel(step("read_page", "https://news.test/story"), step("final", "Done [a1]."))
    model.roles = {"note_taker": FACTS}
    run = await research(ctx, "AAPL", model)
    assert len(pages.requests) == 1 and note_calls(model) == []
    assert by_id(run, 1).read == "too little text to read (paywalled or built by scripts)"


async def test_the_progress_line_counts_the_articles_being_read(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20"), story(2, "BBC", "2026-09-19")]
    for url, *_ in news.results["Apple"]:
        pages.pages[url] = ("text/html", BODY)
    seen = []

    def note(messages):
        seen.append(research_progress(ctx, "AAPL"))
        return FACTS

    await research(ctx, "AAPL", model_reading(note_taker=note))
    assert seen == ["reading 1 of 2", "reading 2 of 2"]


def test_the_pick_is_newest_first_one_per_outlet_skipping_done_stored_data_and_after_the_cutoff():
    from oracle.services.research_tools.reading import pick

    def ev(i, outlet, day, body=None):
        published = datetime.fromisoformat(f"{day}T12:00:00+00:00") if day else None
        return Evidence(i, f"https://x.test/{i}", f"T{i}", outlet, published, False, body)

    evidence = [ev(9, "stored data", "2026-09-21", body="data"), ev(1, "A", "2026-09-20"), ev(2, "A", "2026-09-19"),
                ev(3, "B", "2026-09-18"), ev(4, "C", "2026-09-17"), ev(5, "B", "2026-09-16"), ev(6, "D", None)]
    assert [e.id for e in pick(evidence, set(), 10)] == [1, 3, 4, 6, 2, 5]
    assert [e.id for e in pick(evidence, {3}, 3)] == [1, 4, 5]
    assert [e.id for e in pick(evidence, set(), 10, date(2026, 9, 18))] == [3, 4, 5]


def test_an_outlet_name_is_matched_to_a_domain_when_the_feed_named_no_site():
    from oracle.services.research_tools.reading import from_outlet

    assert from_outlet("https://www.reuters.com/x", "reuters.com", "Reuters")
    assert from_outlet("https://uk.reuters.com/x", "reuters.com", "Reuters")
    assert not from_outlet("https://notreuters.com/x", "reuters.com", "Reuters")
    assert from_outlet("https://finance.yahoo.com/x", None, "Yahoo Finance")
    assert from_outlet("https://www.bbc.co.uk/news/1", None, "BBC")
    assert not from_outlet("https://copycat.test/x", None, "Reuters")
    assert not from_outlet("https://www.reuters.com/x", None, None)
