import asyncio
from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.activity_service import JobRunner
from oracle.services.context import open_context
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        yield c


@pytest.fixture
async def runner(ctx):
    r = JobRunner(ctx)
    yield r
    await r.stop()


class Gate:
    """Work that runs until the test opens it, recording when it started."""

    def __init__(self, name, log, fail=None):
        self.name, self.log, self.fail = name, log, fail
        self.open = asyncio.Event()

    async def __call__(self, ctx=None):
        self.log.append(f"start {self.name}")
        await self.open.wait()
        self.log.append(f"end {self.name}")
        if self.fail:
            raise self.fail
        return self.name


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_jobs_of_one_kind_run_in_order_never_together(runner):
    log = []
    a, b = Gate("a", log), Gate("b", log)
    ja = runner.submit("research", "Research A", a)
    jb = runner.submit("research", "Research B", b)
    await settle()
    assert (ja.state, jb.state) == ("running", "queued") and log == ["start a"]
    a.open.set()
    await settle()
    assert (ja.state, jb.state) == ("done", "running") and log == ["start a", "end a", "start b"]
    b.open.set()
    await runner.wait(jb.id)
    assert jb.state == "done" and jb.result == "b"


async def test_different_kinds_run_side_by_side(runner):
    log = []
    runner.submit("research", "Research A", Gate("a", log))
    runner.submit("prices", "Update prices", Gate("p", log))
    await settle()
    assert sorted(log) == ["start a", "start p"]


async def test_a_failed_job_records_its_error_and_the_queue_moves_on(runner):
    log = []
    bad, good = Gate("bad", log, fail=RuntimeError("model is down")), Gate("good", log)
    jbad = runner.submit("research", "Research bad", bad)
    jgood = runner.submit("research", "Research good", good)
    bad.open.set()
    good.open.set()
    await runner.wait(jgood.id)
    assert (jbad.state, jbad.error) == ("failed", "model is down") and jgood.state == "done"
    assert jbad.finished_at == NOW


async def test_progress_is_read_while_running(runner):
    gate = Gate("a", [])
    job = runner.submit("research", "Research A", gate, progress=lambda: "step 2: news_search")
    await settle()
    assert job.progress() == "step 2: news_search"
    gate.open.set()
    await runner.wait(job.id)
    assert job.progress() is None


async def test_current_work_is_running_first_then_queued_in_order_and_finished_is_paged_apart(runner, ctx):
    gates = [Gate(n, []) for n in "abcd"]
    jobs = [runner.submit("research", f"Research {g.name}", g) for g in gates]
    gates[0].open.set()
    await runner.wait(jobs[0].id)
    await settle()
    assert [j.label for j in runner.current()] == ["Research b", "Research c", "Research d"]
    assert [j.label for j in runner.finished_page(1).items] == ["Research a"]


async def test_the_same_subject_already_waiting_is_not_queued_twice(runner):
    first = runner.submit("research", "Research A", Gate("a", []), subject="AAPL")
    again = runner.submit("research", "Research A", Gate("a2", []), subject="AAPL")
    assert again is first and len(runner.current()) == 1


async def test_cancelling_a_queued_job_means_it_never_runs(runner):
    log = []
    a, b = Gate("a", log), Gate("b", log)
    runner.submit("research", "Research A", a)
    jb = runner.submit("research", "Research B", b)
    runner.cancel(jb.id)
    a.open.set()
    await settle()
    assert jb.state == "cancelled" and "start b" not in log


async def test_cancelling_a_running_job_ends_it_and_the_next_one_starts(runner):
    log = []
    a, b = Gate("a", log), Gate("b", log)
    ja = runner.submit("research", "Research A", a)
    jb = runner.submit("research", "Research B", b)
    await settle()
    runner.cancel(ja.id)
    await settle()
    assert ja.state == "cancelled" and jb.state == "running" and "end a" not in log


async def test_a_finished_job_cannot_be_cancelled(runner):
    from oracle.services.errors import InvalidQuery

    gate = Gate("a", [])
    job = runner.submit("research", "Research A", gate)
    gate.open.set()
    await runner.wait(job.id)
    with pytest.raises(InvalidQuery, match="already done"):
        runner.cancel(job.id)


@pytest.fixture
def gated_stages(monkeypatch):
    """Tick stages that finish at once except `analyse`, which waits for the test."""
    from oracle.services import tick_service

    gate = asyncio.Event()

    async def quick(ctx, say):
        return "done", None

    async def slow(ctx, say):
        await gate.wait()
        return "done", None

    monkeypatch.setattr(tick_service, "STAGES", {"prices": quick, "fx": quick, "analyse": slow, "step": quick})
    return gate


async def test_a_tick_job_shows_the_stage_it_is_on(runner, ctx, gated_stages):
    from oracle.services.activity_service import submit_tick

    job = submit_tick(ctx, runner)
    await settle()
    assert job.progress() == "analyse"
    gated_stages.set()
    await runner.wait(job.id)
    assert job.result.outcome == "ok" and [s.name for s in job.result.stages][-1] == "step"


async def test_cancelling_a_running_tick_records_it_interrupted(runner, ctx, gated_stages):
    from oracle.services.activity_service import submit_tick
    from oracle.services.tick_service import last_tick

    job = submit_tick(ctx, runner)
    await settle()
    runner.cancel(job.id)
    await runner.wait(job.id)
    assert job.state == "cancelled" and last_tick(ctx).outcome == "interrupted"


def london_scheduler(ctx, runner):
    from dataclasses import replace
    from zoneinfo import ZoneInfo

    from oracle.config import ScheduleConfig
    from oracle.services.schedule_service import Scheduler

    # NOW is Wednesday 21:00 in London; the run is due at 21:30 there (20:30 UTC).
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled=True, time="21:30"))
    return Scheduler(ctx, runner, tz=ZoneInfo("Europe/London"))


async def test_a_due_tick_runs_as_a_job_listed_in_activity(runner, ctx):
    scheduler = london_scheduler(ctx, runner)
    ctx.clock.advance(31 * 60)
    run = await scheduler.check()
    assert run.trigger == "scheduled"
    assert [(j.label, j.state) for j in runner.finished_page(1).items] == [("Tick (scheduled)", "done")]


async def test_a_due_tick_is_skipped_while_one_is_running_here(runner, ctx, gated_stages):
    from oracle.services.activity_service import submit_tick

    scheduler = london_scheduler(ctx, runner)
    manual = submit_tick(ctx, runner)
    await settle()
    ctx.clock.advance(31 * 60)
    skipped = await scheduler.check()
    assert skipped.outcome == "skipped" and "already queued or running" in skipped.note
    gated_stages.set()
    await runner.wait(manual.id)


async def test_stopping_a_scheduled_tick_records_it_interrupted_and_it_is_not_run_again(runner, ctx, gated_stages):
    scheduler = london_scheduler(ctx, runner)
    ctx.clock.advance(31 * 60)
    poll = asyncio.create_task(scheduler.check())
    await settle()
    job = runner.active("tick")[0]
    assert job.progress() == "analyse"
    runner.cancel(job.id)
    stopped = await poll
    assert job.state == "cancelled" and stopped.outcome == "interrupted"
    assert await scheduler.check() is None


async def test_finished_jobs_survive_a_restart(ctx, runner):
    gate = Gate("a", [])
    job = runner.submit("research", "Research A", gate, link="#company/A")
    gate.open.set()
    await runner.wait(job.id)
    await runner.stop()
    later = JobRunner(ctx)
    [kept] = later.finished_page(1).items
    assert (kept.id, kept.label, kept.state, kept.link) == (job.id, "Research A", "done", "#company/A")
    assert later.get(job.id).finished_at == job.finished_at
    await later.stop()


DEAD_PID = 999_999_999


async def test_a_starting_server_sweeps_jobs_a_stopped_server_left_queued_or_running(ctx):
    from oracle.services.activity_service import sweep_leftovers
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    running = repo.add("research", "Research A", None, None, "running", NOW, DEAD_PID)
    queued = repo.add("research", "Research B", None, None, "queued", NOW, DEAD_PID)
    sweep_leftovers(ctx)
    runner = JobRunner(ctx)
    states = {j.id: (j.state, j.error) for j in runner.finished_page(1).items}
    assert states[running] == ("interrupted", "interrupted: app closed")
    assert states[queued] == ("cancelled", "not run: app closed")
    await runner.stop()


async def test_the_sweep_leaves_another_live_servers_jobs_and_they_cannot_be_cancelled_here(ctx, runner, monkeypatch):
    from oracle.services import activity_service
    from oracle.services.activity_service import sweep_leftovers
    from oracle.services.errors import InvalidQuery
    from oracle.store.job_history import JobHistoryRepo

    other = JobHistoryRepo(ctx.conn).add("research", "Research A", "A", None, "queued", NOW, 4242)
    monkeypatch.setattr(activity_service, "pid_alive", lambda pid: pid == 4242)
    sweep_leftovers(ctx)
    assert runner.get(other).state == "queued"
    with pytest.raises(InvalidQuery, match="another copy of the app"):
        runner.cancel(other)


async def test_a_job_running_when_the_server_stops_is_interrupted_not_cancelled(ctx):
    runner = JobRunner(ctx)
    job = runner.submit("research", "Research A", Gate("a", []))
    waiting = runner.submit("research", "Research B", Gate("b", []))
    await settle()
    await runner.stop()
    assert (job.state, job.error) == ("interrupted", "interrupted: app closed")
    assert (waiting.state, waiting.error) == ("cancelled", "not run: app closed")


async def test_current_work_leaves_finished_jobs_out(finish_job, runner):
    for n in range(25):
        finish_job(n)
    runner.submit("research", "Research A", Gate("a", []))
    assert [j.label for j in runner.current()] == ["Research A"]


async def test_the_finished_count_follows_the_search(finish_job, runner):
    finish_job(1, label="Research MSFT")
    finish_job(2, label="Research AAPL")
    assert (runner.finished_count(), runner.finished_count("msft")) == (2, 1)


async def test_finished_pages_run_newest_first_with_totals(finish_job, runner):
    for n in range(45):
        finish_job(n)
    first, last = runner.finished_page(1), runner.finished_page(3)
    assert (first.page, first.pages, first.total) == (1, 3, 45)
    assert [j.label for j in first.items][:2] == ["Job 44", "Job 43"] and len(first.items) == 20
    assert [j.label for j in last.items] == [f"Job {n}" for n in range(4, -1, -1)]


async def test_a_page_past_the_end_shows_the_last_page(finish_job, runner):
    for n in range(3):
        finish_job(n)
    beyond = runner.finished_page(9)
    assert (beyond.page, beyond.pages, len(beyond.items)) == (1, 1, 3)


async def test_no_finished_jobs_is_one_empty_page(runner):
    empty = runner.finished_page(1)
    assert (empty.items, empty.page, empty.pages, empty.total) == ([], 1, 1, 0)


async def test_search_matches_label_kind_state_and_error_ignoring_case(finish_job, runner):
    finish_job(1, "research", "Research MSFT")
    finish_job(2, "prices", "Update prices (watchlists)", "failed", "yahoo said no")
    finish_job(3, "tick", "Tick (manual)", "cancelled", "cancelled")
    labels = lambda q: [j.label for j in runner.finished_page(1, q).items]
    assert labels("msft") == ["Research MSFT"]
    assert labels("TICK") == ["Tick (manual)"]
    assert labels("failed") == ["Update prices (watchlists)"]
    assert labels("yahoo") == ["Update prices (watchlists)"]
    assert labels("  ") == labels("") == ["Tick (manual)", "Update prices (watchlists)", "Research MSFT"]
    assert runner.finished_page(1, "nothing like it").total == 0


async def test_search_treats_like_wildcards_literally(finish_job, runner):
    finish_job(1, label="Job 100%")
    finish_job(2, label="Job_2")
    assert [j.label for j in runner.finished_page(1, "%").items] == ["Job 100%"]
    assert [j.label for j in runner.finished_page(1, "_").items] == ["Job_2"]


async def test_waiting_on_an_unknown_job_says_so(runner):
    from oracle.services.errors import UnknownJob

    with pytest.raises(UnknownJob):
        await runner.wait(12345)


async def test_a_scheduled_tick_cancelled_before_it_starts_is_recorded_so_it_is_not_resubmitted(runner, ctx):
    from oracle.services.tick_service import last_tick

    scheduler = london_scheduler(ctx, runner)
    ctx.clock.advance(31 * 60)
    check = asyncio.create_task(scheduler.check())
    await asyncio.sleep(0)  # the check has submitted; the runner has not started it yet
    [queued] = runner.active("tick")
    assert queued.state == "queued"
    runner.cancel(queued.id)
    run = await check
    assert run.outcome == "skipped" and "cancelled" in run.note and last_tick(ctx).id == run.id
    assert await scheduler.check() is None


async def test_new_job_ids_follow_the_stored_ones(ctx, runner):
    first = runner.submit("research", "Research A", Gate("a", []))
    await runner.stop()
    later = JobRunner(ctx)
    assert later.submit("research", "Research B", Gate("b", [])).id > first.id
    await later.stop()


async def test_turning_automation_off_can_stop_only_the_automatic_work(runner, ctx):
    manual = runner.submit("prices", "Update prices", Gate("m", []))
    auto = runner.submit("tick", "Tick (scheduled)", Gate("t", []), automatic=True)
    queued = runner.submit("tick", "Tick (catch-up)", Gate("t2", []), automatic=True)
    await settle()
    stopped = runner.cancel_automatic()
    await runner.wait(auto.id)
    assert {j.label for j in stopped} == {"Tick (scheduled)", "Tick (catch-up)"}
    assert (auto.state, queued.state, manual.state) == ("cancelled", "cancelled", "running")


async def test_scheduled_ticks_are_automatic_and_manual_ones_are_not(runner, ctx, gated_stages):
    from oracle.services.activity_service import submit_tick

    scheduled = submit_tick(ctx, runner, "scheduled")
    gated_stages.set()
    await runner.wait(scheduled.id)
    assert scheduled.automatic and not submit_tick(ctx, runner).automatic


async def test_retrying_a_failed_research_run_resubmits_the_same_symbol(runner, ctx, monkeypatch):
    from types import SimpleNamespace

    from oracle.services import activity_service
    from oracle.services.activity_service import retry
    from oracle.store.job_history import JobHistoryRepo

    monkeypatch.setattr(activity_service, "resolve_symbol", lambda c, s: SimpleNamespace(symbol=s))
    monkeypatch.setattr(activity_service, "check_as_of", lambda c, d: None)
    row_id = JobHistoryRepo(ctx.conn).add("research", "Research AAPL", "AAPL", "#company/AAPL", "queued", NOW,
                                          999_999_999)
    JobHistoryRepo(ctx.conn).update(row_id, "failed", "the model's answers were unusable", NOW, NOW)

    again = retry(ctx, runner, row_id)
    assert again.kind == "research" and again.subject == "AAPL" and again.label == "Research AAPL"


async def test_a_picked_profile_stays_with_the_queued_job_and_its_retry(runner, ctx, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from oracle.config import ModelProfile
    from oracle.services import activity_service
    from oracle.services.activity_service import retry, submit_ask, submit_research
    from oracle.services.errors import UnknownModelProfile
    from oracle.store.job_history import JobHistoryRepo

    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big")})
    monkeypatch.setattr(activity_service, "resolve_symbol", lambda c, s: SimpleNamespace(symbol=s))
    ran = []

    async def research(c, symbol, as_of=None, profile=None):
        ran.append((symbol, profile))
        raise ValueError("model down")

    monkeypatch.setattr(activity_service, "research", research)
    with pytest.raises(UnknownModelProfile):
        submit_research(ctx, runner, "AAPL", profile="huge")
    plain = submit_research(ctx, runner, "AAPL")
    picked = submit_research(ctx, runner, "AAPL", profile="big")
    assert picked is not plain and picked.label == "Research AAPL on big"
    assert submit_research(ctx, runner, "AAPL", profile="big") is picked
    await runner.wait(picked.id)
    assert ran == [("AAPL", None), ("AAPL", "big")]
    assert JobHistoryRepo(ctx.conn).get(picked.id).profile == "big"
    again = retry(ctx, runner, picked.id)
    assert again.profile == "big" and again.label == "Research AAPL on big"
    await runner.wait(again.id)
    assert ran[-1] == ("AAPL", "big")
    with pytest.raises(UnknownModelProfile):
        submit_ask(ctx, runner, "which grid companies?", profile="huge")


async def test_retrying_a_research_replay_keeps_its_day(runner, ctx, monkeypatch):
    from types import SimpleNamespace

    from oracle.services import activity_service
    from oracle.services.activity_service import retry
    from oracle.store.job_history import JobHistoryRepo

    monkeypatch.setattr(activity_service, "resolve_symbol", lambda c, s: SimpleNamespace(symbol=s))
    monkeypatch.setattr(activity_service, "check_as_of", lambda c, d: None)
    row_id = JobHistoryRepo(ctx.conn).add("research", "Replay AAPL as of 2026-09-01", "AAPL@2026-09-01", None,
                                          "queued", NOW, 999_999_999)
    JobHistoryRepo(ctx.conn).update(row_id, "failed", "model down", NOW, NOW)

    again = retry(ctx, runner, row_id)
    assert again.kind == "research" and again.subject == "AAPL@2026-09-01"


async def test_retrying_prices_for_everyone_vs_one_listing(runner, ctx, monkeypatch):
    from types import SimpleNamespace

    from oracle.services import activity_service
    from oracle.services.activity_service import retry
    from oracle.store.job_history import JobHistoryRepo

    monkeypatch.setattr(activity_service, "resolve_symbol", lambda c, s: SimpleNamespace(symbol=s))
    repo = JobHistoryRepo(ctx.conn)
    all_id = repo.add("prices", "Update prices (watchlists)", "*", "#watchlist", "queued", NOW, 999_999_999)
    repo.update(all_id, "failed", "yahoo down", NOW, NOW)
    one_id = repo.add("prices", "Update prices AAPL", "AAPL", "#company/AAPL", "queued", NOW, 999_999_999)
    repo.update(one_id, "cancelled", "cancelled", NOW, NOW)

    again_all = retry(ctx, runner, all_id)
    again_one = retry(ctx, runner, one_id)
    assert (again_all.kind, again_all.subject) == ("prices", "*")
    assert (again_one.kind, again_one.subject) == ("prices", "AAPL")


async def test_retrying_a_tick_is_always_manual(runner, ctx):
    from oracle.services.activity_service import retry
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    row_id = repo.add("tick", "Tick (scheduled)", "tick", "#watchlist", "queued", NOW, 999_999_999)
    repo.update(row_id, "interrupted", "interrupted: app closed", NOW, NOW)

    again = retry(ctx, runner, row_id)
    assert again.kind == "tick" and again.label == "Tick (manual)" and not again.automatic


async def test_retrying_universe_and_merges(runner, ctx):
    from oracle.services.activity_service import retry
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    uni_id = repo.add("universe", "Refresh universe", "universe", "#universe", "queued", NOW, 999_999_999)
    repo.update(uni_id, "failed", "download failed", NOW, NOW)
    merge_id = repo.add("merges", "Proposed merges", "merges", "#universe", "queued", NOW, 999_999_999)
    repo.update(merge_id, "cancelled", "cancelled", NOW, NOW)

    assert retry(ctx, runner, uni_id).kind == "universe"
    assert retry(ctx, runner, merge_id).kind == "merges"


async def test_retrying_a_backfill_backfills_again(runner, ctx):
    from oracle.services.activity_service import retry
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    job_id = repo.add("universe", "Backfill index members", "backfill", "#universe", "queued", NOW, 999_999_999)
    repo.update(job_id, "interrupted", "interrupted: app closed", NOW, NOW)
    again = retry(ctx, runner, job_id)
    assert (again.kind, again.subject, again.label) == ("universe", "backfill", "Backfill index members")


async def test_a_universe_refresh_queues_a_backfill_after_it(runner, monkeypatch):
    from oracle.services import activity_service

    log = []

    async def refresh(c, on_stage):
        log.append("refresh")
        return "status"

    async def backfill(c, on_progress):
        log.append("backfill")
        return "report"

    monkeypatch.setattr(activity_service, "refresh_universe", refresh)
    monkeypatch.setattr(activity_service, "backfill_universe", backfill)
    job = activity_service.submit_universe_refresh(runner)
    await runner.wait(job.id)
    follow = next(j for j in runner.current() if j.subject == "backfill")
    await runner.wait(follow.id)
    assert log == ["refresh", "backfill"] and follow.result == "report"


async def test_retrying_fx_keeps_its_sources_or_the_default(runner, ctx):
    from oracle.services.activity_service import retry
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    default_id = repo.add("fx", "Update FX", "*", None, "queued", NOW, 999_999_999)
    repo.update(default_id, "failed", "no source answered", NOW, NOW)
    picked_id = repo.add("fx", "Update FX (yahoo, ecb)", "yahoo,ecb", None, "queued", NOW, 999_999_999)
    repo.update(picked_id, "failed", "no source answered", NOW, NOW)

    assert retry(ctx, runner, default_id).subject == "*"
    assert retry(ctx, runner, picked_id).subject == "yahoo,ecb"


async def test_retrying_a_done_job_is_refused(runner, ctx):
    from oracle.services.activity_service import retry
    from oracle.services.errors import InvalidQuery

    gate = Gate("a", [])
    job = runner.submit("prices", "Update prices", gate, subject="*")
    gate.open.set()
    await runner.wait(job.id)
    with pytest.raises(InvalidQuery, match="done"):
        retry(ctx, runner, job.id)


async def test_retrying_an_unknown_job_says_so(runner, ctx):
    from oracle.services.activity_service import retry
    from oracle.services.errors import UnknownJob

    with pytest.raises(UnknownJob):
        retry(ctx, runner, 12345)


async def test_a_kind_that_cannot_be_rebuilt_is_refused(runner, ctx):
    from oracle.services.activity_service import retry
    from oracle.services.errors import InvalidQuery
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    row_id = repo.add("mystery", "Some old kind", "x", None, "queued", NOW, 999_999_999)
    repo.update(row_id, "failed", "boom", NOW, NOW)
    with pytest.raises(InvalidQuery, match="cannot be retried"):
        retry(ctx, runner, row_id)


async def test_retry_all_resubmits_every_bad_ending_once(runner, ctx):
    from oracle.services.activity_service import retry_all
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    failed_id = repo.add("prices", "Update prices (watchlists)", "*", None, "queued", NOW, 999_999_999)
    repo.update(failed_id, "failed", "yahoo down", NOW, NOW)
    cancelled_id = repo.add("merges", "Proposed merges", "merges", None, "queued", NOW, 999_999_999)
    repo.update(cancelled_id, "cancelled", "cancelled", NOW, NOW)

    again = retry_all(ctx, runner)
    assert sorted((j.kind, j.subject) for j in again) == [("merges", "merges"), ("prices", "*")]


async def test_retry_all_skips_a_job_whose_picked_profile_is_gone(runner, ctx):
    from oracle.services.activity_service import retry, retry_all, retryable
    from oracle.services.errors import UnknownModelProfile
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    gone = repo.add("ask", "Ask: grid? on big", "7", None, "queued", NOW, 999_999_999, "big")
    repo.update(gone, "failed", "model down", NOW, NOW)
    merges = repo.add("merges", "Proposed merges", "merges", None, "queued", NOW, 999_999_999)
    repo.update(merges, "failed", "boom", NOW, NOW)

    assert [r.id for r in retryable(ctx, runner)] == [merges]
    assert [j.kind for j in retry_all(ctx, runner)] == ["merges"]
    with pytest.raises(UnknownModelProfile):
        retry(ctx, runner, gone)


async def test_retry_all_skips_a_subject_since_redone_successfully(runner, ctx):
    from datetime import timedelta

    from oracle.services.activity_service import retry_all
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    old = repo.add("merges", "Proposed merges", "merges", None, "queued", NOW, 999_999_999)
    repo.update(old, "failed", "boom", NOW, NOW)
    later = NOW + timedelta(minutes=5)
    redone = repo.add("merges", "Proposed merges", "merges", None, "queued", later, 999_999_999)
    repo.update(redone, "done", None, later, later)

    assert retry_all(ctx, runner) == []


async def test_retry_all_skips_a_subject_already_queued_or_running(runner, ctx):
    from oracle.services.activity_service import retry_all
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    failed_id = repo.add("merges", "Proposed merges", "merges", None, "queued", NOW, 999_999_999)
    repo.update(failed_id, "failed", "boom", NOW, NOW)
    runner.submit("merges", "Proposed merges", Gate("m", []), subject="merges")

    assert retry_all(ctx, runner) == []


async def test_retry_all_leaves_unrebuildable_kinds_out(runner, ctx):
    from oracle.services.activity_service import retry_all
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    row_id = repo.add("mystery", "Some old kind", "x", None, "queued", NOW, 999_999_999)
    repo.update(row_id, "failed", "boom", NOW, NOW)

    assert retry_all(ctx, runner) == []


async def test_a_research_run_that_ends_failed_fails_its_job_too(runner, ctx, monkeypatch):
    from types import SimpleNamespace

    from oracle.research.trace import RunStatus
    from oracle.services import activity_service
    from oracle.services.activity_service import submit_research

    async def unusable(c, listing, as_of=None, profile=None):
        return SimpleNamespace(status=RunStatus.FAILED, error="the model's answers were unusable")

    monkeypatch.setattr(activity_service, "research", unusable)
    monkeypatch.setattr(activity_service, "resolve_symbol", lambda c, s: SimpleNamespace(symbol=s))
    job = submit_research(ctx, runner, "AAPL")
    await runner.wait(job.id)
    assert (job.state, job.error) == ("failed", "the model's answers were unusable")


async def test_a_refresh_saved_as_the_app_closes_is_not_reported_failed(runner, monkeypatch):
    from oracle.services import activity_service

    closed = asyncio.new_event_loop()
    closed.close()

    async def refresh(c, on_stage):
        return "status"

    monkeypatch.setattr(activity_service, "refresh_universe", refresh)
    with monkeypatch.context() as m:
        m.setattr(activity_service.asyncio, "get_running_loop", lambda: closed)
        job = activity_service.submit_universe_refresh(runner)
    await runner.wait(job.id)
    assert (job.state, job.result) == ("done", "status")
