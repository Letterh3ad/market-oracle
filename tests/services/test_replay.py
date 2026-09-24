import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from oracle.config import Config, ScheduleConfig
from oracle.market.bars import Bar
from oracle.services import activity_service, replay_service
from oracle.services.activity_service import JobRunner
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.replay_service import (
    ReplayPacer, create_study, pause_study, pick_listings, replay_days, retry_failed, start_study, study_status,
)
from oracle.services.schedule_service import Scheduler
from oracle.store.bars import BarRepo
from oracle.store.health import HealthRepo
from oracle.store.meta import MetaRepo
from oracle.store.replay import ReplayRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import Listing
from tests.fakes.clock import FakeClock

# Thursday 2026-09-24, 12:00 UTC (13:00 in London).
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
FIRST = date(2026, 6, 1)
IT, FIN = "Information Technology", "Financials"


def equity(symbol, name, country, sector, cap="Mega Cap", **extra):
    return Listing(symbol, name, "equity", country, "EX", sector=sector, market_cap=cap, company_key=symbol.lower(),
                   is_primary=True, currency="USD", **extra)


SPREAD = [
    equity("AAPL", "Apple Inc", "United States", IT),
    equity("JPM", "JPMorgan Chase", "United States", FIN),
    equity("HSBA.L", "HSBC Holdings", "United Kingdom", FIN),
    equity("SAP.DE", "SAP SE", "Germany", IT),
    equity("6758.T", "Sony Group", "Japan", IT),
    equity("005930.KS", "Samsung Electronics", "South Korea", IT),
    equity("PETR4.SA", "Petrobras", "Brazil", "Energy"),
]


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        UniverseRepo(c.conn).replace_all(SPREAD)
        MetaRepo(c.conn).set("universe_refreshed_at", "2026-09-24T00:00:00Z")
        yield c


@pytest.fixture
async def runner(ctx):
    r = JobRunner(ctx)
    yield r
    await r.stop()


class FakeResearch:
    """Replays that finish when the test says, each as a run with its own id."""

    def __init__(self):
        self.calls, self.gates, self.fail = [], {}, {}

    async def __call__(self, c, symbol, as_of=None, profile=None):
        key = (symbol, as_of)
        self.calls.append(key)
        gate = self.gates.setdefault(key, asyncio.Event())
        await gate.wait()
        if error := self.fail.pop(key, None):
            raise ValueError(error)
        return SimpleNamespace(id=100 + len(self.calls), status="done", error=None)

    def finish(self, symbol, as_of, error=None):
        if error:
            self.fail[(symbol, as_of)] = error
        self.gates.setdefault((symbol, as_of), asyncio.Event()).set()


@pytest.fixture
def research(monkeypatch):
    fake = FakeResearch()
    monkeypatch.setattr(activity_service, "research", fake)
    return fake


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


def test_one_day_a_month_the_first_weekday_up_to_a_month_ago():
    days = replay_days(date(2025, 6, 1), NOW.date())
    assert days[0] == date(2025, 6, 2)  # the 1st was a Sunday
    assert date(2026, 1, 2) in days  # never New Year's Day
    assert days[-1] == date(2026, 8, 3)  # the 1st was a Saturday; September's has no month of outcome yet
    assert len(days) == 15 and all(d.weekday() < 5 for d in days)
    assert replay_days(date(2025, 6, 3), NOW.date())[0] == date(2025, 7, 1)


def test_the_pick_spreads_over_regions_and_sectors_one_company_each(ctx):
    picked = pick_listings(ctx, FIRST)
    assert [(p.symbol, p.region) for p in picked] == [
        ("AAPL", "US"), ("JPM", "US"), ("HSBA.L", "UK"), ("SAP.DE", "Europe"), ("6758.T", "Japan"),
        ("005930.KS", "Asia"), ("PETR4.SA", "Emerging")]


def test_the_pick_never_takes_tesla_small_dead_or_young_listings(ctx):
    UniverseRepo(ctx.conn).replace_all([
        equity("TSLA", "Tesla, Inc.", "United States", "Consumer Discretionary"),
        equity("AMZN", "Amazon.com", "United States", "Consumer Discretionary", cap="Large Cap"),
        equity("TINY", "Tiny Tech", "United States", IT, cap="Small Cap"),
        equity("DEAD", "Dead Tech", "United States", IT),
        equity("NEWCO", "New Tech", "United States", IT),
        equity("MSFT", "Microsoft", "United States", IT, cap="Large Cap"),
    ])
    HealthRepo(ctx.conn).note_no_data("DEAD", date(2026, 9, 1), dead_after=1)
    BarRepo(ctx.conn).upsert([Bar("NEWCO", date(2026, 7, 1), None, None, None, 1.0, None, None, "USD", "t")], "x")
    assert [p.symbol for p in pick_listings(ctx, FIRST)] == ["MSFT", "AMZN"]


def test_a_new_study_is_paused_with_every_cell_pending_oldest_day_first(ctx):
    s = create_study(ctx, FIRST)
    assert s.state == "paused" and (s.first_day, s.last_day) == (date(2026, 6, 1), date(2026, 8, 3))
    assert s.total == 3 * 7 and s.pending == 21 and s.done == 0
    cells = ReplayRepo(ctx.conn).cells(s.id)
    assert [(c.symbol, c.as_of) for c in cells[:2]] == [("AAPL", date(2026, 6, 1)), ("JPM", date(2026, 6, 1))]
    assert cells[-1].as_of == date(2026, 8, 3)
    assert [p.symbol for p in s.listings][:2] == ["AAPL", "JPM"]


def test_without_a_measured_cutoff_the_first_day_must_be_given(ctx):
    with pytest.raises(InvalidQuery, match="--from"):
        create_study(ctx)


def test_a_measured_cutoff_is_the_default_and_the_earliest_day(ctx, monkeypatch):
    monkeypatch.setattr(replay_service, "measured_cutoff", lambda c: date(2026, 7, 1))
    assert create_study(ctx).first_day == date(2026, 7, 1)
    with pytest.raises(InvalidQuery, match="cutoff"):
        create_study(ctx, date(2026, 6, 1))


def test_no_new_study_while_one_runs(ctx):
    create_study(ctx, FIRST)
    start_study(ctx)
    with pytest.raises(InvalidQuery, match="pause"):
        create_study(ctx, FIRST)


async def test_the_study_never_starts_by_itself(ctx, runner, research):
    create_study(ctx, FIRST)
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None
    await settle()
    assert research.calls == [] and runner.current() == []


async def test_after_start_one_replay_at_a_time_in_queue_order(ctx, runner, research):
    create_study(ctx, FIRST)
    start_study(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    assert job.label == "Replay AAPL as of 2026-06-01" and job.automatic
    await settle()
    assert pacer.check() is None and research.calls == [("AAPL", date(2026, 6, 1))]
    research.finish("AAPL", date(2026, 6, 1))
    await runner.wait(job.id)
    ctx.clock.advance(150)
    nxt = pacer.check()
    assert nxt.subject == "JPM@2026-06-01"
    done = ReplayRepo(ctx.conn).cells(study_status(ctx).id, "done")
    assert [(c.symbol, c.run_id) for c in done] == [("AAPL", 101)]


async def test_pause_stops_new_replays_but_the_running_one_finishes(ctx, runner, research):
    create_study(ctx, FIRST)
    start_study(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    pause_study(ctx)
    research.finish("AAPL", date(2026, 6, 1))
    await runner.wait(job.id)
    assert pacer.check() is None
    s = study_status(ctx)
    assert (s.state, s.done, s.running) == ("paused", 1, ())


async def test_the_pacer_yields_to_other_work_queued_or_running(ctx, runner, research):
    create_study(ctx, FIRST)
    start_study(ctx)
    gate = asyncio.Event()

    async def work(c):
        await gate.wait()

    runner.submit("prices", "Update prices (watchlists)", work)
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None and "Update prices" in pacer.waiting
    gate.set()
    await settle()
    assert pacer.check() is not None and pacer.waiting is None


async def test_the_pacer_yields_to_a_tick_due_within_a_replay_and_its_margin(ctx, runner, research):
    create_study(ctx, FIRST)
    start_study(ctx)
    # 13:00 in London now: a tick at 13:10 is inside 5 minutes plus the margin, one at 13:30 is not.
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled=True, time="13:10", midday_time=""))
    pacer = ReplayPacer(ctx, runner, Scheduler(ctx, runner, tz=ZoneInfo("Europe/London")))
    assert pacer.check() is None and pacer.waiting.startswith("a tick is due")
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled=True, time="13:30", midday_time=""))
    pacer = ReplayPacer(ctx, runner, Scheduler(ctx, runner, tz=ZoneInfo("Europe/London")))
    assert pacer.check() is not None


async def test_the_pacer_yields_to_a_busy_user(ctx, runner, research, monkeypatch):
    create_study(ctx, FIRST)
    start_study(ctx)
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(research_when_idle=True, idle_minutes=15))
    monkeypatch.setattr("oracle.system.idle.idle_seconds", lambda: 60.0)
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None and "in use" in pacer.waiting
    monkeypatch.setattr("oracle.system.idle.idle_seconds", lambda: 16 * 60.0)
    assert pacer.check() is not None


async def test_the_pacer_holds_while_automation_is_off(ctx, runner, research):
    from oracle.config import AutomationConfig

    create_study(ctx, FIRST)
    start_study(ctx)
    ctx.config = replace(ctx.config, automation=AutomationConfig(enabled=False))
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None and "automation" in pacer.waiting


async def test_a_replay_cut_off_by_a_restart_goes_back_to_pending_and_resumes(ctx, research):
    from oracle.services.activity_service import sweep_leftovers

    create_study(ctx, FIRST)
    start_study(ctx)
    first = JobRunner(ctx)
    ReplayPacer(ctx, first).check()
    await settle()
    await first.stop()
    sweep_leftovers(ctx)
    again = JobRunner(ctx)
    try:
        job = ReplayPacer(ctx, again).check()
        assert job.subject == "AAPL@2026-06-01"
        cell = ReplayRepo(ctx.conn).cells(study_status(ctx).id)[0]
        assert (cell.state, cell.attempts, cell.job_id) == ("running", 0, job.id)
    finally:
        await again.stop()


async def run_one(pacer, runner, research, symbol, day, error=None):
    job = pacer.check()
    assert job.subject == f"{symbol}@{day.isoformat()}"
    research.gates.pop((symbol, day), None)
    research.finish(symbol, day, error)
    await runner.wait(job.id)
    research.gates.pop((symbol, day), None)
    return job


async def test_a_failed_cell_is_retried_once_later_then_stays_failed_until_retry(ctx, runner, research):
    UniverseRepo(ctx.conn).replace_all(SPREAD[:2])
    create_study(ctx, date(2026, 8, 1))
    start_study(ctx)
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 8, 3)
    await run_one(pacer, runner, research, "AAPL", day, "model down")
    await run_one(pacer, runner, research, "JPM", day)  # AAPL went to the end of the queue
    s = study_status(ctx)
    assert (s.top_failures, s.retrying, s.failed) == ((("model down", 1),), 1, ())
    await run_one(pacer, runner, research, "AAPL", day, "model down again")
    assert pacer.check() is None
    s = study_status(ctx)
    assert [(c.symbol, c.error, c.attempts) for c in s.failed] == [("AAPL", "model down again", 2)]
    assert pacer.check() is None  # stays failed

    assert retry_failed(ctx) == 1
    await run_one(pacer, runner, research, "AAPL", day, "still down")
    assert pacer.check() is None
    assert [c.error for c in study_status(ctx).failed] == ["still down"]  # one try per Retry


async def test_retry_one_cell_by_id(ctx):
    s = create_study(ctx, FIRST)
    repo = ReplayRepo(ctx.conn)
    a, b = repo.cells(s.id)[:2]
    for c in (a, b):
        repo.mark_failed(c.id, "boom", NOW, requeue=False)
    assert retry_failed(ctx, b.id) == 1
    assert [c.id for c in study_status(ctx).failed] == [a.id]
    with pytest.raises(InvalidQuery):
        retry_failed(ctx, b.id)


async def test_three_failures_in_a_row_pause_the_study(ctx, runner, research):
    create_study(ctx, FIRST)
    start_study(ctx)
    pacer = ReplayPacer(ctx, runner)
    for symbol in ("AAPL", "JPM", "HSBA.L"):
        await run_one(pacer, runner, research, symbol, date(2026, 6, 1), "ollama is not running")
    assert pacer.check() is None
    s = study_status(ctx)
    assert s.state == "paused" and "3 replays failed in a row" in s.paused_reason


async def test_a_replay_cancelled_in_activity_pauses_the_study_and_is_not_counted(ctx, runner, research):
    create_study(ctx, FIRST)
    start_study(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    await settle()
    runner.cancel(job.id)
    await settle()
    assert pacer.check() is None
    s = study_status(ctx)
    assert s.state == "paused" and "cancelled" in s.paused_reason
    first = ReplayRepo(ctx.conn).cells(s.id)[0]
    assert (first.state, first.attempts) == ("pending", 0)


async def test_status_counts_running_age_and_mean_replay_time(ctx, runner, research):
    create_study(ctx, FIRST)
    start_study(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    await settle()
    ctx.clock.advance(120)
    research.finish("AAPL", date(2026, 6, 1))
    await runner.wait(job.id)
    pacer.check()
    ctx.clock.advance(30)
    s = study_status(ctx)
    assert (s.done, s.pending, len(s.running), s.total) == (1, 19, 1, 21)
    assert s.running[0].symbol == "JPM" and s.running[0].age_s == 30
    assert s.mean_replay_s == 120
