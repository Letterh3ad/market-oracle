"""The reading stage: after the loop, the run reads the articles it was shown and notes facts."""

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from oracle.research.llm import ModelError
from oracle.research.trace import NOT_ABOUT, NOTHING, Evidence
from oracle.research.web import WebResult
from oracle.services.research_service import latest_research, research, research_progress
from oracle.store.research import ResearchRepo
from tests.research.test_google_links import BBC_ID, FT_ID, old_style
from tests.fakes.llm import ScriptedModel, step
from tests.services.test_research import (  # noqa: F401
    APPLE_CIK, ARCHIVE, apple_filings, ctx, gdelt, news, pages, searcher, sec, utc, with_contact,
)

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


def reading(ctx, n: int) -> None:
    ctx.config = replace(ctx.config, research=replace(ctx.config.research, read_articles=n))


def by_id(run, article_id: int) -> Evidence:
    return next(e for e in run.evidence if e.id == article_id)


async def test_articles_are_read_newest_first_one_per_outlet_before_repeats(ctx, news, pages):
    reading(ctx, 3)
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


async def test_a_note_garbled_twice_by_one_model_is_not_asked_for_again(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    for _ in range(2):
        await research(ctx, "AAPL", model_reading(note_taker="garbled"))
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    assert note_calls(model) == [] and by_id(run, 1).read == "the model's note was unusable"


async def test_a_backend_failing_does_not_count_toward_giving_up_on_a_note(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    for _ in range(2):
        await research(ctx, "AAPL", model_reading(note_taker=ModelError("ollama timed out")))
    run = await research(ctx, "AAPL", model_reading())
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)


async def test_paywalls_never_stop_the_reads_after_them(ctx, news, pages):
    news.results["Apple"] = [story(n, f"Outlet{n}", "2026-09-20") for n in range(1, 5)] + [
        story(5, "Reuters", "2026-09-19")]
    for n, status in zip(range(1, 5), ("403", "403", "403", "451")):
        pages.pages[f"https://outlet{n}.test/{n}"] = ("status", status)
    pages.pages["https://reuters.test/5"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading())
    assert [by_id(run, n).read for n in range(1, 5)] == ["the page answered HTTP 403", "the page answered HTTP 403",
                                                         "the page answered HTTP 403", "the page answered HTTP 451"]
    assert by_id(run, 5).facts == ("Revenue rose 8% to $95bn",)


async def test_an_unreadable_body_or_any_surprise_costs_only_that_article(ctx, news, pages, monkeypatch, caplog):
    from oracle.services.research_tools import reading as stage

    news.results["Apple"] = [story(1, "Reuters", "2026-09-20"), story(2, "BBC", "2026-09-19"),
                             story(3, "FT", "2026-09-18")]
    pages.pages["https://reuters.test/1"] = ("corrupt", "")
    pages.pages["https://bbc.test/2"] = ("text/html", BODY)
    pages.pages["https://ft.test/3"] = ("text/html", BODY)
    real = stage.read_page

    async def read_page(client, url, *args):
        if url == "https://bbc.test/2":
            raise RuntimeError("boom")
        return await real(client, url, *args)

    monkeypatch.setattr(stage, "read_page", read_page)
    run = await research(ctx, "AAPL", model_reading())
    assert run.status == "done" and by_id(run, 1).read.startswith("the page could not be read (DecodingError")
    assert by_id(run, 2).read == "unexpected error: RuntimeError: boom" and "boom" in caplog.text
    assert by_id(run, 3).facts == ("Revenue rose 8% to $95bn",)
    run = await research(ctx, "AAPL", model := model_reading())
    assert note_calls(model) == [] and by_id(run, 2).read == "unexpected error: RuntimeError: boom"


async def test_a_surprise_while_noting_costs_only_that_note(ctx, news, pages):
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(note_taker=RuntimeError("bad json")))
    assert run.status == "done" and by_id(run, 1).read == "unexpected error: RuntimeError: bad json"


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


GOOGLE = f"https://news.google.com/rss/articles/{FT_ID}"


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
    assert by_id(run, 1).read == ("could not resolve the Google News link: Google's link decoder found no signature "
                                  "on the link's page, and no search result from reuters.com")


BBC_ARTICLE = "https://www.bbc.com/news/articles/cp87m46g392o"


def stub(stub_id: str) -> str:
    return f"https://news.google.com/rss/articles/{stub_id}?oc=5"


async def test_an_older_google_news_link_is_decoded_offline(ctx, news, pages, searcher):
    news.results["Apple"] = [(stub(old_style(BBC_ARTICLE)), "Apple beats estimates", "BBC", utc("2026-09-20"))]
    pages.pages[BBC_ARTICLE] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert fetched(pages) == [BBC_ARTICLE] and searcher.queries == [] and news.stub_pages == []
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)


async def test_a_newer_google_news_link_is_decoded_by_google_through_its_gate(ctx, news, pages, searcher):
    news.results["Apple"] = [(stub(BBC_ID), "Apple beats estimates", "BBC", utc("2026-09-20"))]
    news.decoded[BBC_ID] = BBC_ARTICLE
    pages.pages[BBC_ARTICLE] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert fetched(pages) == [BBC_ARTICLE] and searcher.queries == []
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)
    [page] = news.stub_pages
    assert page.url.params["gl"] == "GB" and "SOCS=" in page.headers["cookie"]
    assert [r.method for r in news.decodes] == ["POST"]
    (gated,) = ctx.conn.execute("SELECT COUNT(*) FROM source_requests WHERE source = 'google_news'").fetchone()
    assert gated == len(news.queries) + 2


async def test_a_failed_decode_falls_back_to_the_headline_search(ctx, news, pages, searcher):
    news.results["Apple"] = [(stub(FT_ID), "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    searcher.results["Apple beats estimates"] = [WebResult("https://www.reuters.com/markets/a", "Apple", "")]
    pages.pages["https://www.reuters.com/markets/a"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert news.decodes == [] and searcher.queries == ["Apple beats estimates"]
    assert fetched(pages) == ["https://www.reuters.com/markets/a"] and by_id(run, 1).facts


@pytest.mark.parametrize("failure", [{"stub_status": 503}, {"stub_status": 429}, {"stub_status": "down"},
                                     {"stub_status": 302}, {"decoder_refuses": True}])
async def test_a_passing_decoder_failure_with_no_search_match_is_tried_again_by_the_next_run(
        ctx, news, pages, searcher, failure):
    news.results["Apple"] = [(stub(BBC_ID), "Apple beats estimates", "BBC", utc("2026-09-20"))]
    news.decoded[BBC_ID] = BBC_ARTICLE
    vars(news).update(failure)
    pages.pages[BBC_ARTICLE] = ("text/html", BODY)
    first = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert pages.requests == [] and len(news.stub_pages) == 1 and searcher.queries == ["Apple beats estimates"]
    assert by_id(first, 1).read.startswith("could not resolve the Google News link: Google's link decoder")
    vars(news).update(stub_status=200, decoder_refuses=False)
    ctx.clock.advance(7200)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",)


async def test_a_throttled_decoder_never_shuts_news_search(ctx, news, pages, searcher):
    news.results["Apple"] = [(stub(BBC_ID), "Apple beats estimates", "BBC", utc("2026-09-20"))]
    news.stub_status = 429
    for _ in range(3):
        await research(ctx, "AAPL", model_reading(), searcher=searcher)
        ctx.clock.advance(60)
    asked = len(news.queries)
    await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert len(news.queries) > asked and len(news.stub_pages) == 4
    (run_429,) = ctx.conn.execute("SELECT run_429 FROM source_state WHERE source = 'google_news'").fetchone()
    assert run_429 == 0


async def test_a_stub_page_cut_before_its_signature_falls_back_to_the_search(ctx, news, pages, searcher):
    from oracle.services.research_tools.google_links import STUB_PAGE_BYTES

    news.results["Apple"] = [(stub(BBC_ID), "Apple beats estimates", "BBC", utc("2026-09-20"))]
    news.decoded[BBC_ID] = BBC_ARTICLE
    news.stub_padding = STUB_PAGE_BYTES
    searcher.results["Apple beats estimates"] = [WebResult("https://www.bbc.com/news/other", "Apple", "")]
    pages.pages["https://www.bbc.com/news/other"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert news.decodes == [] and fetched(pages) == ["https://www.bbc.com/news/other"] and by_id(run, 1).facts


@pytest.mark.parametrize("status", ["404", "410"])
async def test_a_decoded_address_that_is_gone_falls_back_to_the_search(ctx, news, pages, searcher, status):
    news.results["Apple"] = [(stub(BBC_ID), "Apple beats estimates", "BBC", utc("2026-09-20"))]
    news.decoded[BBC_ID] = BBC_ARTICLE
    pages.pages[BBC_ARTICLE] = ("status", status)
    searcher.results["Apple beats estimates"] = [WebResult("https://www.bbc.com/news/other", "Apple", "")]
    pages.pages["https://www.bbc.com/news/other"] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert fetched(pages) == [BBC_ARTICLE, "https://www.bbc.com/news/other"] and by_id(run, 1).facts


async def test_a_decoded_address_refused_otherwise_is_not_searched_again(ctx, news, pages, searcher):
    news.results["Apple"] = [(stub(BBC_ID), "Apple beats estimates", "BBC", utc("2026-09-20"))]
    news.decoded[BBC_ID] = BBC_ARTICLE
    pages.pages[BBC_ARTICLE] = ("status", "403")
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert searcher.queries == [] and by_id(run, 1).read == "the page answered HTTP 403"


@pytest.mark.parametrize("decoded", ["https://copycat.test/apple", "http://127.0.0.1/apple",
                                     "https://reuters.com.copycat.test/apple"])
async def test_a_decoded_address_off_the_outlets_site_or_unsafe_is_never_read(ctx, news, pages, searcher, decoded):
    news.results["Apple"] = [(stub(FT_ID), "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    news.decoded[FT_ID] = decoded
    pages.pages[decoded] = ("text/html", BODY)
    run = await research(ctx, "AAPL", model_reading(), searcher=searcher)
    assert pages.requests == [] and searcher.queries == ["Apple beats estimates"]
    assert by_id(run, 1).read.startswith(f"could not resolve the Google News link: {decoded} is not")


async def test_a_replay_reads_no_article_since_todays_copy_may_differ_from_the_one_then(ctx, news, pages, searcher):
    news.results["Apple before:2026-09-20"] = [(GOOGLE, "Apple beats estimates", "Reuters", utc("2026-09-19")),
                                               story(2, "BBC", "2026-09-18")]
    pages.pages["https://bbc.test/2"] = ("text/html", BODY)
    model = model_reading()
    run = await research(ctx, "AAPL", model, as_of=date(2026, 9, 19), searcher=searcher)
    assert searcher.queries == [] and pages.requests == [] and note_calls(model) == []
    assert all((e.facts, e.read) == ((), None) for e in run.evidence)


async def test_a_replay_shows_no_text_or_notes_a_live_run_took_from_an_article(ctx, news, pages):
    news.results["Apple"] = [story(2, "BBC", "2026-09-18")]
    news.results["Apple before:2026-09-20"] = news.results["Apple"]
    pages.pages["https://bbc.test/2"] = ("text/html", BODY)
    live = await research(ctx, "AAPL", model_reading())
    [article] = [e for e in live.evidence if e.url == "https://bbc.test/2"]
    assert article.facts == ("Revenue rose 8% to $95bn",)
    model = model_reading()
    replay = await research(ctx, "AAPL", model, as_of=date(2026, 9, 19))
    assert (by_id(replay, article.id).facts, by_id(replay, article.id).read) == ((), None)
    assert len(pages.requests) == 1 and "Revenue rose" not in role_input(model, "verdict")
    assert by_id(latest_research(ctx, "AAPL"), article.id).facts == ()
    assert by_id(ResearchRepo(ctx.conn).get(live.id), article.id).facts == article.facts


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
    assert from_outlet("https://www.reuters.com/x", None, "Reuters.com")
    assert not from_outlet("https://copycat.test/x", None, "Reuters")
    assert not from_outlet("https://reutersnews-daily.com/x", None, "Reuters")
    assert not from_outlet("https://reuters-news.com/x", None, "Reuters")
    assert not from_outlet("https://bbc.fakenews.com/x", None, "BBC News")
    assert not from_outlet("https://www.reuters.com/x", None, None)


def test_a_host_reduces_to_its_registered_domain_and_an_outlet_to_its_sites_name():
    from oracle.research.web import outlet_key, registered_domain, site_name

    assert registered_domain("uk.finance.yahoo.com") == "yahoo.com" and site_name("uk.finance.yahoo.com") == "yahoo"
    assert registered_domain("www.bbc.co.uk") == "bbc.co.uk" and site_name("www.bbc.co.uk") == "bbc"
    assert registered_domain("localhost") == "" and site_name("") == ""
    assert registered_domain("fool.co.com") == "co.com" and registered_domain("reuters.com.de") == "com.de"
    assert registered_domain("www.fool.com.au") == "fool.com.au"
    for host, name in [("elcomercio.com.pe", "elcomercio"), ("www.pap.com.pl", "pap"), ("kurier.co.at", "kurier"),
                       ("www.eltiempo.com.co", "eltiempo"), ("www.mof.go.jp", "mof"), ("www.rba.gov.au", "rba")]:
        assert site_name(host) == name, host
    assert site_name("reuters.com.de") == "com" and site_name("reuters.co.com") == "co"
    assert outlet_key("The Motley Fool") == "fool" and outlet_key("The Wall Street Journal") == "wsj"
    assert outlet_key("Reuters") == "reuters" and outlet_key("Fool's Gold Weekly") == "foolsgoldweekly"


LISTED_OUTLETS = [("The Motley Fool", "www.fool.com"), ("The Motley Fool UK", "www.fool.co.uk"),
                  ("The Motley Fool Canada", "www.fool.ca"), ("The Motley Fool Australia", "www.fool.com.au"),
                  ("The Wall Street Journal", "www.wsj.com"), ("Financial Times", "www.ft.com"),
                  ("The New York Times", "www.nytimes.com"), ("Investor's Business Daily", "www.investors.com")]


@pytest.mark.parametrize("outlet, host", LISTED_OUTLETS)
def test_every_listed_outlet_matches_its_registered_site(outlet, host):
    from oracle.research.web import outlet_key
    from oracle.services.research_tools.reading import from_outlet

    assert from_outlet(f"https://{host}/a", None, outlet)
    assert outlet_key(outlet) == host.split(".")[1]


def test_every_listed_outlet_is_reachable_from_its_name_past_the_filler_words():
    from oracle.research.web import OUTLET_SITES, outlet_sites

    reached = set().union(*(outlet_sites(outlet) for outlet, _ in LISTED_OUTLETS))
    assert reached == set().union(*OUTLET_SITES.values())


def test_an_outlet_is_matched_at_its_registered_site_when_name_and_domain_differ_in_form():
    from oracle.services.research_tools.reading import from_outlet

    assert from_outlet("https://www.fool.com/investing/x", None, "The Motley Fool")
    assert from_outlet("https://www.fool.co.uk/x", None, "Motley Fool")
    assert from_outlet("https://www.wsj.com/x", None, "The Wall Street Journal")
    assert from_outlet("https://www.ft.com/content/x", None, "Financial Times")
    assert from_outlet("https://www.morningstar.com/x", "global.morningstar.com", "Morningstar")
    assert from_outlet("https://uk.finance.yahoo.com/x", "finance.yahoo.com", "Yahoo Finance")
    assert not from_outlet("https://fool.com.evil.test/x", None, "The Motley Fool")
    assert not from_outlet("https://foolnews.com/x", None, "The Motley Fool")
    assert not from_outlet("https://www.thetimes.com/x", None, "Financial Times")
    for host in ("ft.xyz", "ft.co.uk", "www.ft.com.xyz"):
        assert not from_outlet(f"https://{host}/a", None, "Financial Times")
    assert not from_outlet("https://fool.co.com/a", None, "The Motley Fool")
    assert not from_outlet("https://reuters.com.de/a", None, "Reuters")
    assert not from_outlet("https://www.fool.com/x", None, "Fool's Gold Weekly")
    assert not from_outlet("https://other.substack.com/p/x", "jane.substack.com", "Jane's Notes")
    assert not from_outlet("https://www.yahoo.com.evil.test/x", "finance.yahoo.com", "Yahoo Finance")
    assert not from_outlet("https://www.morningstar.com/x", "global.morningstar.com", "Morning Brief")


FIXTURES = Path(__file__).parent.parent / "fixtures" / "edgar"
FILED_10Q = f"{ARCHIVE}/000032019326000077/aapl-20260627.htm"
FILED_8K = f"{ARCHIVE}/000032019326000090/aapl-8k.htm"


INDEX_8K = f"{ARCHIVE}/000032019326000090/0000320193-26-000090-index.htm"
EXHIBIT = f"{ARCHIVE}/000032019326000090/a8-kex991q3202606272026.htm"


def press_release(sec, folder: str = "000032019326000090", index: str = INDEX_8K) -> None:
    """The 8-K's filing index, naming its EX-99.1, and the exhibit."""
    listed = (FIXTURES / "aapl-8k-index.htm").read_text(encoding="utf-8")
    sec.documents[index] = listed.replace("000032019326000018", folder)
    sec.documents[f"{ARCHIVE}/{folder}/a8-kex991q3202606272026.htm"] = (FIXTURES / "aapl-ex991.htm").read_text("utf-8")


def sec_documents(sec) -> None:
    apple_filings(sec)
    sec.documents[FILED_10Q] = (FIXTURES / "aapl-10q.htm").read_text(encoding="utf-8")
    sec.documents[FILED_8K] = (FIXTURES / "aapl-8k.htm").read_text(encoding="utf-8")


def noted_texts(model) -> list[str]:
    return [c[0][1]["content"] for c in note_calls(model)]


async def test_a_us_listings_filings_are_read_first_trimmed_to_what_bears_on_the_price(ctx, news, pages, sec):
    reading(ctx, 3)
    with_contact(ctx)
    sec_documents(sec)
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20"), story(2, "BBC", "2026-09-19")]
    for url, *_ in news.results["Apple"]:
        pages.pages[url] = ("text/html", BODY)
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    assert fetched(pages) == ["https://reuters.test/1"]
    quarterly, event, article = noted_texts(model)
    assert quarterly.startswith("[a3] 2026-08-01 SEC: 10-Q Quarterly report\n\nItem 2. Management’s Discussion")
    assert "Total net sales increased 8%" in quarterly and "New tariffs announced" in quarterly
    assert "94,930" not in quarterly and "TABLE OF CONTENTS" not in quarterly
    assert event.startswith("[a4] 2026-09-10 SEC: 8-K Current report\n\nItem 2.02") and "Inline XBRL" not in event
    assert article.startswith("[a1] 2026-09-20 Reuters")
    documents = [r for r in sec.requests if "/Archives/" in str(r.url)]
    assert [str(r.url) for r in documents] == [FILED_10Q, FILED_8K, INDEX_8K]
    assert all("faye@example.com" in r.headers["user-agent"] for r in documents)
    assert by_id(run, 3).facts == ("Revenue rose 8% to $95bn",) and by_id(run, 3).outlet == "SEC"
    (text,) = ctx.conn.execute("SELECT text FROM article_texts WHERE article_id = 3").fetchone()
    assert text.startswith("Item 2. Management’s") and "SIGNATURES" not in text


async def test_filings_take_at_most_two_slots_and_a_second_run_reads_them_no_more(ctx, sec):
    with_contact(ctx)
    sec_documents(sec)
    sec.filings[APPLE_CIK].insert(0, ("8-K", "2026-09-15", "0000320193-26-000095", "aapl-8k2.htm", "8-K"))
    sec.filings[APPLE_CIK].append(("10-K", "2025-10-31", "0000320193-25-000079", "aapl-20250927.htm", "10-K"))
    sec.documents[f"{ARCHIVE}/000032019326000095/aapl-8k2.htm"] = sec.documents[FILED_8K]
    sec.documents[f"{ARCHIVE}/000032019325000079/aapl-20250927.htm"] = (FIXTURES / "aapl-10k.htm").read_text("utf-8")

    def model_listing_filings():
        model = ScriptedModel(step("filings"), step("final", "Done."))
        model.roles = {"note_taker": FACTS}
        return model

    model = model_listing_filings()
    run = await research(ctx, "AAPL", model)
    assert len(note_calls(model)) == 2
    assert [e.title for e in run.evidence if e.outlet == "SEC"] == [
        "8-K", "8-K Current report", "10-Q Quarterly report", "10-K"]
    model = model_listing_filings()
    await research(ctx, "AAPL", model)
    [event] = noted_texts(model)
    assert event.startswith("[a2] 2026-09-10 SEC: 8-K Current report")
    assert not any("aapl-20250927" in str(r.url) for r in sec.requests)


async def test_a_replay_reads_only_filings_made_before_its_cutoff(ctx, sec):
    with_contact(ctx)
    sec_documents(sec)
    model = model_reading()
    run = await research(ctx, "AAPL", model, as_of=date(2026, 9, 1))
    [quarterly] = noted_texts(model)
    assert "10-Q Quarterly report" in quarterly
    assert [e.title for e in run.evidence if e.outlet == "SEC"] == ["10-Q Quarterly report"]


async def test_a_filing_with_no_headings_is_read_from_its_first_paragraph_and_sec_trouble_never_fails_the_run(ctx, sec):
    with_contact(ctx)
    apple_filings(sec)
    sec.documents[FILED_10Q] = "<html><body><p>" + "Plain quarterly text. " * 2000 + "</p></body></html>"
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    [quarterly] = noted_texts(model)
    assert "Plain quarterly text." in quarterly and run.status == "done"
    assert by_id(run, 2).read == "the page answered HTTP 404"
    sec.filings.clear()
    run = await research(ctx, "AAPL", model_reading())
    assert run.status == "done"


def sec_evidence(run, title: str) -> Evidence:
    return next(e for e in run.evidence if e.outlet == "SEC" and e.title == title)


async def test_an_earnings_8k_is_read_from_its_press_release_exhibit_then_its_other_items(ctx, sec):
    with_contact(ctx)
    sec_documents(sec)
    press_release(sec)
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    event = sec_evidence(run, "8-K Current report")
    [noted] = [t for t in noted_texts(model) if t.startswith(f"[a{event.id}]")]
    assert noted.startswith(f"[a{event.id}] 2026-09-10 SEC: 8-K Current report\n\nEX-99.1\n"
                            "Apple reports fourth quarter results\nRecord September quarter revenue")
    assert "retire as Chief Operating Officer" in noted and "A copy is furnished" not in noted
    documents = [r for r in sec.requests if "/Archives/" in str(r.url)]
    assert [str(r.url) for r in documents] == [FILED_10Q, FILED_8K, INDEX_8K, EXHIBIT]
    assert all("faye@example.com" in r.headers["user-agent"] for r in documents)
    assert event.facts == ("Revenue rose 8% to $95bn",)
    url, text = ctx.conn.execute("SELECT url, text FROM article_texts WHERE article_id = ?", (event.id,)).fetchone()
    assert url == EXHIBIT and text.startswith("EX-99.1\nApple reports")


def no_exhibit(sec) -> None:
    del sec.documents[EXHIBIT]


def no_press_exhibit(sec) -> None:
    sec.documents[INDEX_8K] = sec.documents[INDEX_8K].replace("EX-99.1", "EX-10.1")


def thin_exhibit(sec) -> None:
    sec.documents[EXHIBIT] = '<html><body><img src="release.jpg"></body></html>'


@pytest.mark.parametrize("unreadable", [no_exhibit, no_press_exhibit, thin_exhibit])
async def test_an_earnings_8k_without_a_readable_press_exhibit_keeps_its_own_items(ctx, sec, unreadable):
    with_contact(ctx)
    sec_documents(sec)
    press_release(sec)
    unreadable(sec)
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    event = sec_evidence(run, "8-K Current report")
    [noted] = [t for t in noted_texts(model) if t.startswith(f"[a{event.id}]")]
    assert noted.startswith(f"[a{event.id}] 2026-09-10 SEC: 8-K Current report\n\nItem 2.02 Results")


@pytest.mark.parametrize("url, failure", [
    (INDEX_8K, httpx.ConnectTimeout("timed out")),
    (EXHIBIT, httpx.Response(429, headers={"Retry-After": "3600"})),
])
async def test_sec_trouble_on_the_exhibit_is_passing_so_the_next_run_reads_it(ctx, sec, url, failure):
    with_contact(ctx)
    sec_documents(sec)
    press_release(sec)
    sec.failures[url] = failure
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    event = sec_evidence(run, "8-K Current report")
    assert "unavailable" in event.read
    assert not any(t.startswith(f"[a{event.id}]") for t in noted_texts(model))
    del sec.failures[url]
    ctx.clock.advance(3600)
    model = model_reading()
    run = await research(ctx, "AAPL", model)
    [noted] = [t for t in noted_texts(model) if t.startswith(f"[a{event.id}]")]
    assert "EX-99.1\nApple reports" in noted


async def test_a_replay_reads_the_exhibit_of_an_8k_filed_before_its_cutoff_and_nothing_after(ctx, sec):
    with_contact(ctx)
    sec_documents(sec)
    press_release(sec)
    sec.filings[APPLE_CIK].insert(0, ("8-K", "2026-09-15", "0000320193-26-000095", "aapl-8k2.htm", "8-K"))
    sec.documents[f"{ARCHIVE}/000032019326000095/aapl-8k2.htm"] = sec.documents[FILED_8K]
    press_release(sec, "000032019326000095", f"{ARCHIVE}/000032019326000095/0000320193-26-000095-index.htm")
    model = model_reading()
    run = await research(ctx, "AAPL", model, as_of=date(2026, 9, 12))
    assert [e.title for e in run.evidence if e.outlet == "SEC"] == ["8-K Current report", "10-Q Quarterly report"]
    assert any("EX-99.1\nApple reports" in t for t in noted_texts(model))
    assert EXHIBIT in [str(r.url) for r in sec.requests]
    assert not any("000032019326000095" in str(r.url) for r in sec.requests)


async def test_a_listing_abroad_reads_a_results_page_its_web_search_found_before_news(ctx, news, pages, searcher):
    reading(ctx, 2)
    news.results["Sony"] = [story(1, "Reuters", "2026-09-20"), story(2, "BBC", "2026-09-19")]
    searcher.results["Sony results"] = [
        WebResult("https://www.sony.com/en/ir/library/presen/er/pdf/26q1_sonypre.pdf", "Q1 FY2026 Results (PDF)", ""),
        WebResult("https://www.sony.com/en/ir/news/2026/q1-results.html", "Consolidated Financial Results for Q1", ""),
        WebResult("https://blog.test/sony-fans", "Sony fan page", "")]
    for url in ("https://reuters.test/1", "https://bbc.test/2", "https://www.sony.com/en/ir/news/2026/q1-results.html",
                "https://blog.test/sony-fans"):
        pages.pages[url] = ("text/html", BODY)
    model = ScriptedModel(step("news_search", "Sony"), step("web_search", "Sony results"), step("final", "Done."))
    model.roles = {"note_taker": FACTS}
    await research(ctx, "6758.T", model, searcher=searcher)
    assert fetched(pages) == ["https://www.sony.com/en/ir/news/2026/q1-results.html", "https://reuters.test/1"]


def test_the_pick_puts_up_to_two_of_the_companys_own_reports_first_and_leaves_the_others():
    from oracle.services.research_tools.reading import pick

    def ev(i, outlet, day):
        return Evidence(i, f"https://x.test/{i}", f"T{i}", outlet, datetime.fromisoformat(f"{day}T12:00:00+00:00"), False)

    evidence = [ev(1, "A", "2026-09-20"), ev(2, "SEC", "2026-09-15"), ev(3, "SEC", "2026-09-10"),
                ev(4, "SEC", "2026-08-01"), ev(5, "B", "2026-07-01")]
    assert [e.id for e in pick(evidence, set(), 10, first=[4, 2, 3])] == [4, 2, 1, 5]
    assert [e.id for e in pick(evidence, {4}, 10, first=[4, 2, 3])] == [2, 3, 1, 5]
    assert [e.id for e in pick(evidence, set(), 10, date(2026, 9, 12), first=[4, 2, 3])] == [4, 3, 5]
    assert [e.id for e in pick(evidence, set(), 1, first=[4, 2, 3])] == [4]


def test_a_report_page_is_an_undated_web_result_named_like_results_or_a_report_and_not_a_pdf():
    from oracle.services.research_tools.reading import report_page

    def ev(url, title, day=None):
        published = datetime.fromisoformat(f"{day}T12:00:00+00:00") if day else None
        return Evidence(1, url, title, None, published, False)

    assert report_page(ev("https://www.sony.com/en/ir/news/q1.html", "Consolidated Financial Results for Q1"))
    assert report_page(ev("https://www.bmw.com/geschaeftsbericht-2025.html", "BMW Group"))
    assert report_page(ev("https://ir.test/x", "Integrated Report 2026"))
    assert not report_page(ev("https://www.sony.com/q1.pdf", "Consolidated Financial Results for Q1"))
    assert not report_page(ev("https://reuters.test/1", "Sony financial results beat", "2026-09-20"))
    assert not report_page(ev("https://blog.test/sony", "Sony fan page"))

