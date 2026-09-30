import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from oracle.config import Config, LayaConfig
from oracle.research.laya import MODEL_TAG
from oracle.research.news import Article
from oracle.services import laya_service
from oracle.services.activity_service import JobRunner, retry, submit_laya
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.laya_service import company_articles, laya_scores, score_articles, score_company
from oracle.services.universe_service import refresh_universe
from oracle.store.articles import ArticleRepo
from oracle.store.job_history import JobHistoryRepo
from oracle.store.research import ResearchRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.laya import FakeLaya
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def ctx(tmp_path):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo())
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def shown_to_research(ctx, symbol, *articles, source="google_news"):
    """Articles a research run on `symbol` was shown, as research stores them."""
    ids = ArticleRepo(ctx.conn).save(list(articles), source, "2026-09-29T10:00:00Z")
    repo = ResearchRepo(ctx.conn)
    repo.add_evidence(repo.start(symbol, "ollama", "m", "2026-09-29T10:00:00Z"), ids)
    return ids


def news(title, day=28, summary=None, body=None):
    return Article(f"https://news.test/{title}", title, "Wire",
                   datetime(2026, 9, day, 9, tzinfo=timezone.utc) if day else None, summary=summary, body=body)


def test_a_companys_articles_are_what_its_research_read_and_its_filings_never_our_own_notes(ctx):
    a, b = shown_to_research(ctx, "AAPL", news("Apple beats"), news("Apple falls"))
    [mine] = shown_to_research(ctx, "AAPL", news("stored data summary", body="x"), source="oracle")
    [filing] = ArticleRepo(ctx.conn).save([news("AAPL 10-Q")], "sec_edgar", "2026-09-29T10:00:00Z", query="AAPL")
    shown_to_research(ctx, "KO", news("Coke rises"))
    assert sorted(company_articles(ctx, "AAPL.MI")) == sorted([a, b, filing]), "any listing reads the company's"
    assert mine not in company_articles(ctx, "AAPL")


def test_scoring_stores_each_article_for_the_company_under_the_model_tag(ctx):
    shown_to_research(ctx, "AAPL", news("Apple beats estimates", summary="Revenue up"), news("Oil falls"))
    fake = FakeLaya()
    run = score_company(ctx, "AAPL", scorer=fake)
    assert (run.symbol, run.model, run.articles, run.scored, run.already) == ("AAPL", "fake-laya", 2, 2, 0)
    [asked] = fake.batches
    assert {(i.company, i.headline, i.snippet) for i in asked} == {
        ("Apple Inc. (AAPL)", "Apple beats estimates", "Revenue up"), ("Apple Inc. (AAPL)", "Oil falls", None)}
    rows = {s.title: s for s in laya_scores(ctx, "AAPL", model="fake-laya")}
    beat = rows["Apple beats estimates"]
    assert (beat.relation, beat.direction, beat.event, beat.relevant, beat.model) == (
        "own", "up", "earnings", 0.9, "fake-laya")
    assert beat.direction_probs == {"up": 0.8, "down": 0.1, "none": 0.1} and beat.scored_at.startswith("2026-09-30T12:00")
    assert rows["Oil falls"].relevant == 0.2


def test_the_same_article_company_and_model_is_never_scored_twice(ctx):
    shown_to_research(ctx, "AAPL", news("Apple beats"))
    score_company(ctx, "AAPL", scorer=FakeLaya())
    again = FakeLaya()
    run = score_company(ctx, "AAPL", scorer=again)
    assert (run.scored, run.already, again.batches) == (0, 1, [])
    other = score_company(ctx, "AAPL", scorer=FakeLaya(tag="laya-next"))
    assert other.scored == 1, "a new model scores it under its own tag"


def test_nothing_to_score_never_loads_the_model(ctx, monkeypatch):
    shown_to_research(ctx, "AAPL", news("Apple beats"))
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: FakeLaya(tag=MODEL_TAG))
    score_company(ctx, "AAPL")
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: pytest.fail("loaded with nothing to do"))
    assert score_company(ctx, "AAPL").already == 1


def test_a_model_it_loaded_itself_is_closed_after(ctx, monkeypatch):
    shown_to_research(ctx, "AAPL", news("Apple beats"))
    fake = FakeLaya(tag=MODEL_TAG)
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: fake)
    score_company(ctx, "AAPL")
    assert fake.closed


def test_articles_go_in_batches_and_each_batch_is_kept_as_it_finishes(ctx):
    ids = shown_to_research(ctx, "AAPL", *(news(f"Apple item {n}") for n in range(5)))
    stops = iter([False, False, True])
    fake = FakeLaya()
    run = score_articles(ctx, "AAPL", ids, scorer=fake, batch=2, stop=lambda: next(stops))
    assert [len(b) for b in fake.batches] == [2, 2] and run.scored == 4 and run.stopped
    assert len(laya_scores(ctx, "AAPL", model="fake-laya")) == 4


def test_it_waits_while_live_model_work_runs_and_gives_the_gpu_back_meanwhile(ctx, monkeypatch, laya_waits):
    shown_to_research(ctx, "AAPL", news("Apple beats"), news("Apple falls"))
    loads: list[FakeLaya] = []

    def load(c):
        loads.append(FakeLaya(tag=MODEL_TAG))
        return loads[-1]

    monkeypatch.setattr(laya_service, "load_scorer", load)
    busy = iter([None, "Research MSFT", "Research MSFT", None, None])
    progress: list[str] = []
    run = score_company(ctx, "AAPL", batch=1, busy=lambda: next(busy), on_progress=progress.append)
    assert run.scored == 2 and laya_waits == [laya_service.YIELD_STEP_S] * 2
    assert "waiting for Research MSFT to finish" in progress[1]
    assert len(loads) == 2 and loads[0].closed and loads[1].closed, "closed while waiting, loaded again after"


def test_laya_switched_off_refuses_to_score(ctx):
    ctx.config = replace(ctx.config, laya=LayaConfig(enabled=False))
    shown_to_research(ctx, "AAPL", news("Apple beats"))
    with pytest.raises(InvalidQuery, match="Laya is off"):
        score_company(ctx, "AAPL")


def test_history_use_leaves_out_undated_articles_and_scores_known_after_the_day(ctx):
    shown_to_research(ctx, "AAPL", news("Apple beats", day=20), news("Apple rises", day=27), news("Apple undated", day=None))
    score_company(ctx, "AAPL", scorer=FakeLaya())
    every = laya_scores(ctx, "AAPL", model="fake-laya")
    assert [s.title for s in every] == ["Apple undated", "Apple rises", "Apple beats"], "newest known first"
    undated = every[0]
    assert (undated.published_at, undated.known_at, undated.dated) == (None, "2026-09-29T10:00:00Z", False)
    history = laya_scores(ctx, "AAPL", model="fake-laya", history=True, known_by="2026-09-25T00:00:00Z")
    assert [s.title for s in history] == ["Apple beats"]


def test_scores_read_under_the_current_model_by_default(ctx):
    shown_to_research(ctx, "AAPL", news("Apple beats"))
    score_company(ctx, "AAPL", scorer=FakeLaya(tag="old"))
    assert laya_scores(ctx, "AAPL") == []
    score_company(ctx, "AAPL", scorer=FakeLaya(tag=MODEL_TAG))
    assert [s.model for s in laya_scores(ctx, "AAPL")] == [MODEL_TAG]


@pytest.fixture
async def runner(ctx):
    r = JobRunner(ctx)
    yield r
    await r.stop()


async def test_the_activity_job_scores_the_company_and_names_it(ctx, runner, monkeypatch):
    shown_to_research(ctx, "AAPL", news("Apple beats"))
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: FakeLaya(tag=MODEL_TAG))
    job = submit_laya(ctx, runner, "aapl")
    assert (job.kind, job.subject, job.label, job.link) == ("laya", "AAPL", "Laya AAPL", "#company/AAPL")
    done = await runner.wait(job.id)
    assert done.state == "done" and done.result.scored == 1


async def test_the_job_gives_way_to_research_running_beside_it(ctx, runner, monkeypatch):
    shown_to_research(ctx, "AAPL", news("Apple beats"))
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: FakeLaya(tag=MODEL_TAG))
    research = asyncio.Event()

    async def researching(c):
        await research.wait()

    other = runner.submit("research", "Research MSFT", researching, subject="MSFT")
    await asyncio.sleep(0)
    laya_waits_seen = []

    def research_ends(seconds):
        # Scoring holds this test's loop (the server runs it on a thread), so the research is
        # marked finished here, as its own thread would.
        laya_waits_seen.append(seconds)
        other.state = "done"
        research.set()

    monkeypatch.setattr(laya_service, "wait", research_ends)
    job = submit_laya(ctx, runner, "AAPL")
    done = await runner.wait(job.id)
    assert done.result.scored == 1 and laya_waits_seen == [laya_service.YIELD_STEP_S]


async def test_a_failed_laya_job_can_be_retried_and_off_refuses_at_once(ctx, runner):
    row_id = JobHistoryRepo(ctx.conn).add("laya", "Laya AAPL", "AAPL", "#company/AAPL", "queued", NOW, 999_999_999)
    JobHistoryRepo(ctx.conn).update(row_id, "failed", "boom", NOW, NOW)
    again = retry(ctx, runner, row_id)
    assert (again.kind, again.subject) == ("laya", "AAPL")
    ctx.config = replace(ctx.config, laya=LayaConfig(enabled=False))
    with pytest.raises(InvalidQuery, match="Laya is off"):
        submit_laya(ctx, runner, "KO")
