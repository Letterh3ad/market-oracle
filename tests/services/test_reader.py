from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from oracle.config import AutomationConfig, Config, LayaConfig, ScheduleConfig
from oracle.research.news import Article
from oracle.services import laya_service, reader_service
from oracle.services.activity_service import JobRunner, _reader_waits, retry, submit_reader
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, JobAlreadyRunning
from oracle.services.jobs_service import single_flight
from oracle.services.laya_service import laya_scores
from oracle.services.reader_service import PairGroup, last_reader_run, plan_reader, score_reader, waits_anywhere
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.articles import ArticleRepo
from oracle.store.job_history import JobHistoryRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.reader import FakeReader
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
TAG = "ollama:fake"


@pytest.fixture
async def ctx(tmp_path):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo())
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def articles(ctx, *titles):
    return ArticleRepo(ctx.conn).save([Article(f"https://news.test/{t}", t, "Wire", datetime(2026, 9, 28, 9, tzinfo=timezone.utc))
                                       for t in titles], "google_news", "2026-09-29T10:00:00Z")


def scores(ctx, symbol):
    return {s.title: (s.relation, s.direction) for s in laya_scores(ctx, symbol, model=TAG)}


@pytest.fixture
def pairs(ctx):
    """AAPL's own news, KO's own news, and KO's news again as AAPL's competitor news; one KO
    article is in AAPL's own backfill too."""
    a1, a2, both, k1 = articles(ctx, "Apple beats", "Apple falls", "Apple and Coca-Cola rise", "Coca-Cola recall")
    return [PairGroup("AAPL", "AAPL", "own", (a1, a2, both)), PairGroup("KO", "KO", "own", (both, k1)),
            PairGroup("AAPL", "KO", "competitor", (both, k1))]


def test_each_article_is_read_once_for_its_subject_and_pairs_reuse_that_reading(ctx, pairs):
    fake = FakeReader()
    run = score_reader(ctx, groups=pairs, reader=fake, batch=3)
    asked = [(i.company.split()[0], i.headline) for b in fake.batches for i in b]
    assert len(asked) == len(set(asked)) == 5, "own AAPL 3 + own KO 2, the competitor group asks nothing"
    assert [len(b) for b in fake.batches] == [3, 2], "batches mix companies"
    assert scores(ctx, "AAPL") == {"Apple beats": ("own", "up"), "Apple falls": ("own", "down"),
                                   "Apple and Coca-Cola rise": ("own", "up"), "Coca-Cola recall": ("competitor", "down")}
    assert scores(ctx, "KO") == {"Apple and Coca-Cola rise": ("own", "up"), "Coca-Cola recall": ("own", "down")}
    assert (run.subject_articles, run.scored, run.already, run.copied, run.unparsed) == (5, 5, 0, 1, 0)
    assert run.model == TAG and run.stopped is None and fake.closed is False
    assert last_reader_run(ctx).scored == 5


def test_a_rerun_reads_nothing_again_and_leaves_laya_scores_alone(ctx, pairs):
    laya_service.score_articles(ctx, "AAPL", list(pairs[0].ids), scorer=__import__("tests.fakes.laya", fromlist=["x"]).FakeLaya())
    score_reader(ctx, groups=pairs, reader=FakeReader())
    again = FakeReader()
    run = score_reader(ctx, groups=pairs, reader=again)
    assert again.batches == [] and (run.scored, run.already, run.copied) == (0, 5, 0)
    assert len(laya_scores(ctx, "AAPL", model="fake-laya")) == 3


def test_own_news_stays_own_even_when_a_competitor_copy_could_land_first(ctx, pairs):
    """Cancelled after KO's reading only: the copy for AAPL skips the article in AAPL's own set."""
    order = [pairs[1], pairs[2], pairs[0]]
    fake = FakeReader()
    calls = iter([False, True])
    run = score_reader(ctx, groups=order, reader=fake, batch=2, stop=lambda: next(calls, True))
    assert run.stopped == "cancelled" and run.scored == 2
    assert scores(ctx, "AAPL") == {"Coca-Cola recall": ("competitor", "down")}
    score_reader(ctx, groups=order, reader=FakeReader())
    assert scores(ctx, "AAPL")["Apple and Coca-Cola rise"] == ("own", "up")


def test_unanswered_headlines_stay_unscored_and_are_asked_next_run(ctx, pairs):
    run = score_reader(ctx, groups=pairs, reader=FakeReader(unparsed={"Apple falls"}))
    assert run.unparsed == 1 and "Apple falls" not in scores(ctx, "AAPL")
    fake = FakeReader()
    score_reader(ctx, groups=pairs, reader=fake)
    assert [i.headline for b in fake.batches for i in b] == ["Apple falls"]


def test_it_waits_while_live_work_runs_and_says_so(ctx, pairs, monkeypatch):
    waits, lines = [], []
    monkeypatch.setattr(laya_service, "wait", waits.append)
    busy = iter(["the scheduled tick at 22:00 UTC", "the scheduled tick at 22:00 UTC", None])
    fake = FakeReader()
    run = score_reader(ctx, groups=pairs, reader=fake, batch=2, busy=lambda: next(busy, None), on_progress=lines.append)
    assert run.scored == 5 and len(waits) == 2
    assert fake.released == 1, "the model's memory goes to the live work while it waits"
    assert any("waiting for the scheduled tick at 22:00 UTC" in line for line in lines)
    assert "2 of 5 read (0 unanswered), 100.00 a second, about 0.0 h left" in lines


def test_cancel_while_waiting_stops_between_batches(ctx, pairs, monkeypatch):
    monkeypatch.setattr(laya_service, "wait", lambda s: None)
    stops = iter([False, False, True])
    run = score_reader(ctx, groups=pairs, reader=FakeReader(), busy=lambda: "research AAPL", stop=lambda: next(stops, True))
    assert run.stopped == "cancelled" and run.scored == 0


def test_one_run_at_a_time_across_processes(ctx, pairs):
    with single_flight(ctx, reader_service.JOB, 60):
        with pytest.raises(JobAlreadyRunning):
            score_reader(ctx, groups=pairs, reader=FakeReader())


def test_the_plan_counts_each_subjects_article_once_and_estimates_from_the_measured_rate(ctx, pairs):
    plan = plan_reader(ctx, groups=pairs, model="fake")
    assert (plan.subject_articles, plan.unscored, plan.pairs) == (5, 5, 6)
    assert plan.rate == reader_service.UNKNOWN_RATE and "not in the bake-off" in plan.basis and plan.estimate_s == pytest.approx(5 / plan.rate, abs=0.1)
    score_reader(ctx, groups=pairs, reader=FakeReader(unparsed={"Apple falls"}))
    assert plan_reader(ctx, groups=pairs).unscored == 5, "the configured model has read nothing"
    plan = plan_reader(ctx, groups=pairs, model="fake")
    assert plan.unscored == 1 and plan.rate == pytest.approx(100.0) and "last run" in plan.basis


@pytest.fixture
async def runner(ctx):
    r = JobRunner(ctx)
    yield r
    await r.stop()


async def test_the_activity_job_reads_the_list_history_and_a_retry_resumes(ctx, runner, pairs, monkeypatch):
    fake = FakeReader()
    scopes = []
    monkeypatch.setattr(reader_service, "reader_groups", lambda c, w, own_only: scopes.append(own_only) or pairs)
    monkeypatch.setattr(reader_service, "load_reader", lambda c: fake)
    watch(ctx, "AAPL")
    job = submit_reader(ctx, runner, own_only=True)
    assert (job.kind, job.link) == ("reader", "#ledgers") and "qwen2.5:32b" in job.label and "own news" in job.label
    done = await runner.wait(job.id)
    assert done.state == "done" and done.result.scored == 5 and fake.closed and scopes == [True]
    again = await runner.wait(submit_reader(ctx, runner, own_only=True).id)
    assert again.state == "done" and again.result.already == 5 and again.result.scored == 0
    row_id = JobHistoryRepo(ctx.conn).add("reader", job.label, job.subject, job.link, "queued", NOW, 999_999_999)
    JobHistoryRepo(ctx.conn).update(row_id, "interrupted", "app closed", NOW, NOW)
    resumed = retry(ctx, runner, row_id)
    assert (resumed.kind, resumed.subject) == ("reader", job.subject)
    await runner.wait(resumed.id)
    assert scopes == [True, True, True], "a retry keeps the scope"


def test_the_groups_are_06s_history_pairs(ctx, monkeypatch):
    asked = []
    monkeypatch.setattr(reader_service, "history_pairs", lambda c, w, **kw: asked.append((w, kw)) or [])
    reader_service.reader_groups(ctx, "main", own_only=True, only=["AAPL"])
    assert asked == [("main", {"only": ["AAPL"], "own_only": True})]


async def test_the_job_waits_for_model_work_anywhere_and_a_scheduled_tick(ctx, runner, monkeypatch):
    monkeypatch.setattr(laya_service, "live_model_work", lambda c, locks: None)
    ctx.clock.advance(5 * 24 * 3600)  # a Monday: ticks run on weekdays
    assert _reader_waits(ctx, runner) is None
    at = (ctx.clock.utcnow() + timedelta(minutes=25)).astimezone().strftime("%H:%M")
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled=True, time=at, midday_time=None),
                         automation=AutomationConfig(enabled=True))
    assert "scheduled tick" in _reader_waits(ctx, runner)
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled=False))
    monkeypatch.setattr(laya_service, "live_model_work", lambda c, locks: "research" if "tick" in locks else None)
    assert _reader_waits(ctx, runner) == "research", "another process's research"


def test_the_reader_and_laya_history_wait_on_each_others_locks_never_their_own(ctx):
    from oracle.services.laya_history_service import JOB as HISTORY_JOB, history_waits_anywhere

    assert waits_anywhere(ctx) is None and history_waits_anywhere(ctx) is None
    with single_flight(ctx, HISTORY_JOB, 60):
        assert waits_anywhere(ctx) == "laya history"
        assert history_waits_anywhere(ctx) is None, "not on its own lock"
    with single_flight(ctx, reader_service.JOB, 60):
        assert history_waits_anywhere(ctx) == "reader"
        assert waits_anywhere(ctx) is None, "not on its own lock"


async def test_the_reader_and_laya_history_jobs_wait_for_a_regime_ai_backtest(ctx, runner):
    import asyncio

    from oracle.services.activity_service import BACKTEST_SEP, REGIME_AI_PART, _laya_history_waits

    hold = asyncio.Event()

    async def replaying(c):
        await hold.wait()

    plain = runner.submit("backtest", "Backtest main", replaying, subject=BACKTEST_SEP.join(("main", "a", "b", "10.0", "0.0")))
    await asyncio.sleep(0.05)
    assert _reader_waits(ctx, runner) is None and _laya_history_waits(runner) is None, "a plain backtest uses no model"
    hold.set()
    await runner.wait(plain.id)
    hold.clear()
    ai = runner.submit("backtest", "Backtest main, with regime AI", replaying,
                       subject=BACKTEST_SEP.join(("main", "x", "y", "10.0", "0.0", REGIME_AI_PART)))
    await asyncio.sleep(0.05)
    assert _reader_waits(ctx, runner) == _laya_history_waits(runner) == "Backtest main, with regime AI"
    hold.set()
    await runner.wait(ai.id)


def test_the_last_run_says_why_it_stopped_after_an_error_or_a_takeover(ctx, pairs):
    class Broken(FakeReader):
        def __init__(self, exc):
            super().__init__()
            self.exc = exc

        def score(self, items):
            raise self.exc

    with pytest.raises(RuntimeError):
        score_reader(ctx, groups=pairs, reader=Broken(RuntimeError("disk full")))
    assert last_reader_run(ctx).stopped == "failed: disk full"
    assert last_reader_run(ctx).summary.startswith("stopped early (failed: disk full)")
    with pytest.raises(JobAlreadyRunning):
        score_reader(ctx, groups=pairs, reader=Broken(JobAlreadyRunning("reader was taken over")))
    assert last_reader_run(ctx).stopped == "taken over by another process"


async def test_laya_off_refuses_the_job_at_once(ctx, runner):
    ctx.config = replace(ctx.config, laya=LayaConfig(enabled=False))
    with pytest.raises(InvalidQuery, match="Laya is off"):
        submit_reader(ctx, runner)
