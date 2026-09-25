import asyncio
import json
import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from oracle.config import AutomationConfig, Config, ModelProfile, ScheduleConfig
from oracle.jobs import JobLock
from oracle.services import activity_service
from oracle.services.activity_service import JobRunner, retry, retryable, sweep_leftovers
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.replay_score import replay_report
from oracle.services.replay_service import (
    BACKOFF, LONG_BACKOFF, MAX_BACKOFFS, ReplayPacer, create_study, pause_study, pick_listings, replace_listing, retry_failed,
    start_study, study_status,
)
from oracle.services.models_service import RESEARCH_STAGES as STAGES
from oracle.services.schedule_service import Scheduler
from oracle.store.health import HealthRepo
from oracle.store.job_history import JobHistoryRepo
from oracle.store.meta import MetaRepo
from oracle.store.replay import ReplayRepo
from oracle.store.research import ResearchRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import Listing
from tests.fakes.clock import FakeClock
from tests.fakes.cutoff import store_probe
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

# Thursday 2026-09-24, 12:00 UTC (13:00 in London).
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
FIRST = date(2026, 6, 1)
IT, FIN = "Information Technology", "Financials"
WEEKDAYS = [d for d in (date(2026, 5, 1) + timedelta(n) for n in range(146)) if d.weekday() < 5]


def equity(symbol, name, country, sector, exchange, currency, cap="Mega Cap", key=None, primary=True, **extra):
    return Listing(symbol, name, "equity", country, exchange, sector=sector, market_cap=cap, currency=currency,
                   company_key=key or symbol.lower(), is_primary=primary, **extra)


SPREAD = [
    equity("AAPL", "Apple Inc", "United States", IT, "NMS", "USD"),
    equity("JPM", "JPMorgan Chase", "United States", FIN, "NYQ", "USD"),
    equity("HSBA.L", "HSBC Holdings", "United Kingdom", FIN, "LSE", "GBp"),
    equity("SAP.DE", "SAP SE", "Germany", IT, "GER", "EUR"),
    equity("6758.T", "Sony Group", "Japan", IT, "JPX", "JPY"),
    equity("005930.KS", "Samsung Electronics", "South Korea", IT, "KSC", "KRW"),
    equity("PETR4.SA", "Petrobras", "Brazil", "Energy", "SAO", "BRL"),
]
SYMBOLS = [x.symbol for x in SPREAD]


def priced(yahoo, symbol, days=WEEKDAYS, currency="USD"):
    yahoo.add(symbol, currency, "America/New_York", [bar(d.isoformat(), 10.0) for d in days])


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for symbol in SYMBOLS:
        priced(y, symbol)
    return y


@pytest.fixture
def ctx(tmp_path, yahoo):
    web = FakeWeb(yahoo)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        UniverseRepo(c.conn).replace_all(SPREAD)
        MetaRepo(c.conn).set("universe_refreshed_at", "2026-09-24T00:00:00Z")
        store_probe(c.conn, c.config)
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


async def started(ctx, first=FIRST, **kw):
    s = await create_study(ctx, first, **kw)
    start_study(ctx)
    return s


def cells(ctx, state=None):
    return ReplayRepo(ctx.conn).cells(study_status(ctx).id, state)


def fail_for_good(ctx, cell, error="boom"):
    repo = ReplayRepo(ctx.conn)
    for _ in range(cell.allowed):
        repo.mark_failed(cell.id, error, NOW)


def london(ctx, runner, hhmm, midday="", fills=()):
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled=True, time=hhmm, midday_time=midday,
                                                             fill_times=fills))
    return Scheduler(ctx, runner, tz=ZoneInfo("Europe/London"))


# The pick and the cells

async def test_the_pick_spreads_over_regions_and_sectors_one_company_each(ctx):
    picked, _ = await pick_listings(ctx, FIRST)
    assert [(p.symbol, p.region) for p in picked] == [
        ("AAPL", "US"), ("JPM", "US"), ("HSBA.L", "UK"), ("SAP.DE", "Europe"), ("6758.T", "Japan"),
        ("005930.KS", "Asia"), ("PETR4.SA", "Emerging")]


async def test_the_pick_takes_the_regions_own_line_on_its_main_board(ctx, yahoo):
    UniverseRepo(ctx.conn).replace_all([
        # A New York ADR is the primary; the home line is the one wanted.
        equity("TSM", "Taiwan Semiconductor", "Taiwan", IT, "NYQ", "USD", key="tsmc"),
        equity("2330.TW", "Taiwan Semiconductor", "Taiwan", IT, "TAI", "TWD", key="tsmc", primary=False),
        # A Berlin line as primary, the Tokyo one beside it.
        equity("DKI.BE", "Daikin Industries", "Japan", "Industrials", "BER", "EUR", key="daikin"),
        equity("6367.T", "Daikin Industries", "Japan", "Industrials", "JPX", "JPY", key="daikin", primary=False),
        # A UK company listed only in New York has no UK line.
        equity("LIN", "Linde plc", "United Kingdom", "Materials", "NYQ", "USD"),
    ])
    for symbol in ("TSM", "2330.TW", "DKI.BE", "6367.T", "LIN"):
        priced(yahoo, symbol)
    assert [p.symbol for p in (await pick_listings(ctx, FIRST))[0]] == ["6367.T", "2330.TW"]


async def test_the_pick_never_takes_tesla_small_dead_young_stale_or_unpriced_listings(ctx, yahoo):
    UniverseRepo(ctx.conn).replace_all([
        equity("TSLA", "Tesla, Inc.", "United States", "Consumer Discretionary", "NMS", "USD"),
        equity("AMZN", "Amazon.com", "United States", "Consumer Discretionary", "NMS", "USD", cap="Large Cap"),
        equity("TINY", "Tiny Tech", "United States", IT, "NMS", "USD", cap="Small Cap"),
        equity("DEAD", "Dead Tech", "United States", IT, "NMS", "USD"),
        equity("NEWCO", "New Tech", "United States", IT, "NMS", "USD"),
        equity("OLDCO", "Old Tech", "United States", IT, "NYQ", "USD"),
        equity("NOPRICE", "Unpriced Tech", "United States", IT, "NYQ", "USD"),
        equity("MSFT", "Microsoft", "United States", IT, "NMS", "USD", cap="Large Cap"),
    ])
    for symbol in ("TSLA", "AMZN", "TINY", "DEAD", "MSFT"):
        priced(yahoo, symbol)
    priced(yahoo, "NEWCO", [d for d in WEEKDAYS if d >= date(2026, 7, 1)])
    priced(yahoo, "OLDCO", [d for d in WEEKDAYS if d <= date(2026, 8, 1)])
    HealthRepo(ctx.conn).note_no_data("DEAD", date(2026, 9, 1), dead_after=1)
    said = []
    picked, left_out = await pick_listings(ctx, FIRST, said.append)
    assert [p.symbol for p in picked] == ["MSFT", "AMZN"] and len(left_out) == 18
    assert any("NEWCO's prices start 2026-07-01" in s for s in said)
    assert any("OLDCO's last price is" in s for s in said)
    assert any("no price data for NOPRICE" in s for s in said)


async def test_a_new_study_is_paused_with_a_cell_per_listing_and_month_on_its_first_trading_day(ctx, yahoo):
    # 1 July is a US holiday here: AAPL's replay that month is its first trading day after.
    priced(yahoo, "AAPL", [d for d in WEEKDAYS if d != date(2026, 7, 1)])
    s = await create_study(ctx, FIRST)
    assert s.state == "paused" and (s.first_day, s.last_day) == (date(2026, 6, 1), date(2026, 8, 3))
    assert s.total == 3 * 7 and s.pending == 21 and s.done == 0
    listed = cells(ctx)
    assert [(c.symbol, c.as_of) for c in listed[:2]] == [("AAPL", date(2026, 6, 1)), ("JPM", date(2026, 6, 1))]
    assert ("AAPL", date(2026, 7, 2)) in [(c.symbol, c.as_of) for c in listed]
    assert listed[-1].as_of == date(2026, 8, 3) and all(c.allowed == 2 for c in listed)


async def test_symbols_replace_the_pick_and_are_checked_the_same_way(ctx):
    s = await create_study(ctx, FIRST, symbols=["jpm", "6758.T"])
    assert [(p.symbol, p.region, p.sector) for p in s.listings] == [("JPM", "US", FIN), ("6758.T", "Japan", IT)]
    UniverseRepo(ctx.conn).replace_all([*SPREAD, equity("TSLA", "Tesla, Inc.", "United States", IT, "NMS", "USD")])
    with pytest.raises(InvalidQuery, match="never list"):
        await create_study(ctx, FIRST, symbols=["TSLA"])
    with pytest.raises(InvalidQuery, match="no price data"):
        UniverseRepo(ctx.conn).replace_all([*SPREAD, equity("ZZZ", "Zed", "United States", IT, "NMS", "USD")])
        await create_study(ctx, FIRST, symbols=["ZZZ"])


async def test_a_listing_can_be_replaced_only_before_the_first_replay(ctx, runner, research, yahoo):
    UniverseRepo(ctx.conn).replace_all([*SPREAD, equity("MSFT", "Microsoft", "United States", IT, "NMS", "USD")])
    priced(yahoo, "MSFT")
    await create_study(ctx, FIRST)
    s = await replace_listing(ctx, "aapl", "MSFT")
    assert [p.symbol for p in s.listings][:2] == ["MSFT", "JPM"] and s.listings[0].region == "US"
    assert s.total == 21 and "AAPL" not in {c.symbol for c in cells(ctx)}
    with pytest.raises(InvalidQuery, match="already in the study"):
        await replace_listing(ctx, "MSFT", "JPM")
    start_study(ctx)
    ReplayPacer(ctx, runner).check()
    with pytest.raises(InvalidQuery, match="before the study's first replay"):
        await replace_listing(ctx, "JPM", "AAPL")


async def test_a_stage_model_never_probed_refuses_create_naming_it(ctx):
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")},
                         model_slots={"verdict": "big"})
    for first in (None, FIRST):
        with pytest.raises(InvalidQuery, match="probe big first"):
            await create_study(ctx, first)


async def test_the_first_cell_is_strictly_after_the_measured_cutoff(ctx):
    store_probe(ctx.conn, ctx.config, date(2026, 6, 30))
    s = await create_study(ctx)
    assert s.first_day > date(2026, 6, 30) and s.cutoff == date(2026, 6, 30)
    assert min(c.as_of for c in cells(ctx)) == s.first_day
    with pytest.raises(InvalidQuery, match="on or before the measured cutoff 2026-06-30"):
        await create_study(ctx, date(2026, 6, 30))
    assert (await create_study(ctx, date(2026, 7, 1))).first_day >= date(2026, 7, 1)


async def test_no_new_study_while_any_replay_runs(ctx, runner, research):
    await started(ctx)
    ReplayPacer(ctx, runner).check()
    pause_study(ctx)
    with pytest.raises(InvalidQuery, match="a replay is running"):
        await create_study(ctx, FIRST)


# The pacer

async def test_the_study_never_starts_by_itself(ctx, runner, research):
    await create_study(ctx, FIRST)
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None
    await settle()
    assert research.calls == [] and runner.current() == []


async def test_after_start_one_replay_at_a_time_in_queue_order(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    assert job.label == "Replay AAPL as of 2026-06-01" and job.automatic
    await settle()
    assert pacer.check() is None and research.calls == [("AAPL", date(2026, 6, 1))]
    research.finish("AAPL", date(2026, 6, 1))
    await runner.wait(job.id)
    nxt = pacer.check()
    assert nxt.subject == "JPM@2026-06-01"
    assert [(c.symbol, c.run_id) for c in cells(ctx, "done")] == [("AAPL", 101)]


async def test_each_poll_is_stored_with_why_it_waits(ctx, runner, research):
    await started(ctx)
    ctx.config = replace(ctx.config, automation=AutomationConfig(enabled=False))
    ReplayPacer(ctx, runner).check()
    s = study_status(ctx)
    assert s.polled_at == ctx.clock.utcnow() and s.waiting == "automation is off"
    assert s.to_json()["polled_at"] == ctx.clock.utcnow().isoformat()


async def test_pause_stops_new_replays_but_the_running_one_finishes(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    pause_study(ctx)
    research.finish("AAPL", date(2026, 6, 1))
    await runner.wait(job.id)
    assert pacer.check() is None
    s = study_status(ctx)
    assert (s.state, s.done, s.running) == ("paused", 1, ())


async def test_the_pacer_yields_to_other_work_queued_or_running(ctx, runner, research):
    await started(ctx)
    gate = asyncio.Event()

    async def work(c):
        await gate.wait()

    runner.submit("prices", "Update prices (watchlists)", work)
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None and "Update prices" in pacer.waiting
    gate.set()
    await settle()
    assert pacer.check() is not None and pacer.waiting is None


async def test_the_pacer_yields_to_work_another_process_holds(ctx, runner, research):
    await started(ctx)
    lock = JobLock(ctx.conn, ctx.clock, pid_alive=lambda pid: True, pid=os.getpid() + 1)
    handle = lock.acquire("research", 3600)
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None and "another process" in pacer.waiting
    lock.release(handle)
    assert pacer.check() is not None


async def test_the_pacer_yields_to_a_tick_due_within_the_longest_replay_and_its_margin(ctx, runner, research):
    await started(ctx)
    # 13:00 in London now: a tick at 13:10 is inside 5 minutes plus the margin, one at 13:30 is not.
    pacer = ReplayPacer(ctx, runner, london(ctx, runner, "13:10"))
    assert pacer.check() is None and pacer.waiting.startswith("a tick is due")
    assert ReplayPacer(ctx, runner, london(ctx, runner, "13:30")).check() is not None


async def test_the_pacer_yields_to_a_fill_pass_as_to_a_tick(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner, london(ctx, runner, "22:30", fills=("09:30", "13:10")))
    assert pacer.check() is None and pacer.waiting.startswith("a fill pass is due")
    scheduler = london(ctx, runner, "22:30", fills=("13:30",))
    pacer = ReplayPacer(ctx, runner, scheduler)
    job = pacer.check()
    await settle()
    ctx.clock.advance(29 * 60 + 10)
    assert pacer.check() is None and "the fill pass due at" in pacer.waiting
    await settle()
    assert job.state == "cancelled"


async def test_the_pacer_holds_between_a_ticks_time_and_the_poll_that_submits_it(ctx, runner, research):
    await started(ctx)
    scheduler = london(ctx, runner, "13:05")
    due = NOW + timedelta(minutes=5)
    ctx.clock.advance((due - ctx.clock.utcnow()).total_seconds() + 5)
    assert scheduler.upcoming().at > due + timedelta(hours=1)  # the banner has moved on
    pacer = ReplayPacer(ctx, runner, scheduler)
    assert pacer.check() is None and pacer.waiting.startswith("a tick is due")
    await scheduler.check()  # submits the tick and waits for it
    assert pacer.check() is not None


async def test_the_pacer_yields_to_a_busy_user(ctx, runner, research, monkeypatch):
    await started(ctx)
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(research_when_idle=True, idle_minutes=15))
    monkeypatch.setattr("oracle.system.idle.idle_seconds", lambda: 60.0)
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check() is None and "in use" in pacer.waiting
    monkeypatch.setattr("oracle.system.idle.idle_seconds", lambda: 16 * 60.0)
    assert pacer.check() is not None


async def test_a_running_replay_gives_way_to_a_tick_about_to_fire(ctx, runner, research):
    await started(ctx)
    scheduler = london(ctx, runner, "13:30")
    pacer = ReplayPacer(ctx, runner, scheduler)
    job = pacer.check()
    await settle()
    ctx.clock.advance(29 * 60 + 10)
    assert pacer.check() is None and pacer.waiting.startswith("stopped the replay of AAPL")
    assert pacer.waiting.endswith("it goes back in the queue")
    await settle()
    assert job.state == "cancelled"
    pacer.check()
    s = study_status(ctx)
    first = cells(ctx)[0]
    assert (s.state, first.state, first.attempts) == ("running", "pending", 0)


async def test_a_running_replay_gives_way_to_a_research_button(ctx, runner, research):
    from oracle.services.activity_service import submit_research

    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    await settle()
    mine = submit_research(ctx, runner, "JPM")
    pacer.check()
    await settle()
    assert job.state == "cancelled" and mine.state == "running"
    pacer.check()
    assert study_status(ctx).state == "running" and cells(ctx)[0].state == "pending"


async def test_a_replay_cut_off_by_a_restart_goes_back_to_pending_and_resumes(ctx, research):
    await started(ctx)
    first = JobRunner(ctx)
    ReplayPacer(ctx, first).check()
    await settle()
    await first.stop()
    sweep_leftovers(ctx)
    again = JobRunner(ctx)
    try:
        job = ReplayPacer(ctx, again).check()
        assert job.subject == "AAPL@2026-06-01"
        cell = cells(ctx)[0]
        assert (cell.state, cell.attempts, cell.job_id) == ("running", 0, job.id)
    finally:
        await again.stop()


async def test_a_replay_still_queued_when_the_server_stopped_goes_back_to_pending(ctx, research):
    await started(ctx)
    first = JobRunner(ctx)
    ReplayPacer(ctx, first).check()
    await first.stop()  # before its turn came: "not run"
    again = JobRunner(ctx)
    try:
        ReplayPacer(ctx, again).check()
        await settle()
        assert cells(ctx)[0].attempts == 0 and research.calls[-1] == ("AAPL", date(2026, 6, 1))
    finally:
        await again.stop()


async def test_another_live_servers_replay_is_left_running(ctx, runner, research):
    await started(ctx)
    repo = ReplayRepo(ctx.conn)
    cell = cells(ctx)[0]
    history = JobHistoryRepo(ctx.conn)
    live = history.add("research", "Replay AAPL", "AAPL@2026-06-01", None, "running", NOW, os.getppid())
    repo.mark_running(cell.id, live, NOW)
    ReplayPacer(ctx, runner).check()
    assert repo.cell(cell.id).state == "running"
    gone = history.add("research", "Replay AAPL", "AAPL@2026-06-01", None, "running", NOW, 999_999_999)
    repo.mark_running(cell.id, gone, NOW)
    ReplayPacer(ctx, runner).check()
    assert repo.cell(cell.id).job_id != gone


async def test_a_replay_done_before_a_restart_is_read_back_from_history(ctx, runner):
    await started(ctx)
    repo = ReplayRepo(ctx.conn)
    cell = cells(ctx)[0]
    run_id = ResearchRepo(ctx.conn).start("AAPL", "ollama", "m", "2026-09-24T11:00:00Z", cell.as_of)
    history = JobHistoryRepo(ctx.conn)
    job_id = history.add("research", "Replay AAPL", "AAPL@2026-06-01", None, "queued", NOW, 999_999_999)
    history.update(job_id, "done", None, NOW, NOW)
    repo.mark_running(cell.id, job_id, NOW)
    pause_study(ctx)
    ReplayPacer(ctx, runner).check()
    assert (repo.cell(cell.id).state, repo.cell(cell.id).run_id) == ("done", run_id)


async def run_one(pacer, runner, research, symbol, day, error=None):
    job = pacer.check()
    assert job.subject == f"{symbol}@{day.isoformat()}"
    research.finish(symbol, day, error)
    await runner.wait(job.id)
    research.gates.pop((symbol, day), None)
    return job


async def test_a_failed_cell_is_retried_once_later_then_stays_failed_until_retry(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=["AAPL", "JPM"])
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 8, 3)
    await run_one(pacer, runner, research, "AAPL", day, "model down")
    await run_one(pacer, runner, research, "JPM", day)  # AAPL went to the end of the queue
    s = study_status(ctx)
    assert (s.top_failures, s.retrying, s.failed) == ((("model down", 1),), 1, ())
    await run_one(pacer, runner, research, "AAPL", day, "bad reply")
    assert pacer.check() is None
    s = study_status(ctx)
    assert [(c.symbol, c.error, c.attempts) for c in s.failed] == [("AAPL", "bad reply", 2)]
    assert (s.finished, s.complete, s.to_json()["complete"]) == (True, False, False)
    assert pacer.check() is None  # stays failed

    assert retry_failed(ctx) == 1
    await run_one(pacer, runner, research, "AAPL", day, "odd reply")
    assert pacer.check() is None
    assert [(c.error, c.attempts) for c in study_status(ctx).failed] == [("odd reply", 3)]  # one try per Retry


async def test_retry_one_cell_by_id(ctx):
    await create_study(ctx, FIRST)
    a, b = cells(ctx)[:2]
    fail_for_good(ctx, a)
    fail_for_good(ctx, b)
    assert retry_failed(ctx, b.id) == 1
    assert [c.id for c in study_status(ctx).failed] == [a.id]
    assert ReplayRepo(ctx.conn).cell(b.id).attempts == 2
    with pytest.raises(InvalidQuery):
        retry_failed(ctx, b.id)


async def test_a_manual_retry_gives_a_cell_its_outage_refunds_back(ctx):
    from oracle.services.activity_service import _adopt_replay_cell

    await create_study(ctx, FIRST)
    repo = ReplayRepo(ctx.conn)
    a, b, c = cells(ctx)[:3]
    for cell in (a, b, c):
        repo.refund([cell.id], 2)
        repo.refund([cell.id], 2)
        fail_for_good(ctx, cell)
    retry_failed(ctx, a.id)
    assert repo.cell(a.id).refunds == 0 and repo.cell(b.id).refunds == 2
    retry_failed(ctx)
    assert repo.cell(b.id).refunds == 0
    repo.refund([c.id], 2)
    repo.refund([c.id], 2)
    fail_for_good(ctx, repo.cell(c.id))
    _adopt_replay_cell(ctx, SimpleNamespace(id=77, profile=None), c.symbol, c.as_of)
    assert repo.cell(c.id).refunds == 0


async def probe_fails(pacer, runner, research, error):
    job = pacer.check()
    symbol, day = research_key(job)
    research.finish(symbol, day, error.format(symbol=symbol))
    await runner.wait(job.id)
    research.gates.clear()
    assert pacer.check() is None  # settles the probe: another back-off


async def test_replays_failing_alike_back_off_without_using_attempts_and_never_pause(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 6, 1)
    for symbol in ("AAPL", "JPM", "HSBA.L"):
        await run_one(pacer, runner, research, symbol, day, f"ollama is not running ({symbol})")
    assert pacer.check() is None and pacer.waiting.startswith("backing off")
    assert "(1 back-off; the last error: ollama is not running (HSBA.L))" in pacer.waiting
    assert all(c.attempts == 0 for c in cells(ctx)) and study_status(ctx).state == "running"
    for _ in range(MAX_BACKOFFS):
        ctx.clock.advance(BACKOFF.total_seconds())
        await probe_fails(pacer, runner, research, "ollama is not running ({symbol})")
    ctx.clock.advance(BACKOFF.total_seconds())
    assert pacer.check() is None and "5 back-offs" in pacer.waiting  # now every 2 hours
    ctx.clock.advance((LONG_BACKOFF - BACKOFF).total_seconds())
    await probe_fails(pacer, runner, research, "ollama is not running ({symbol})")
    s = study_status(ctx)
    assert (s.state, s.paused_reason) == ("running", None) and "6 back-offs" in s.waiting
    assert all(c.attempts == 0 for c in cells(ctx))


async def test_a_restart_mid_back_off_resumes_the_back_off(ctx, research):
    await started(ctx)
    first = JobRunner(ctx)
    pacer = ReplayPacer(ctx, first)
    day = date(2026, 6, 1)
    for symbol in ("AAPL", "JPM", "HSBA.L"):
        await run_one(pacer, first, research, symbol, day, "ollama is not running")
    assert pacer.check() is None and pacer.waiting.startswith("backing off")
    await first.stop()
    again = JobRunner(ctx)
    try:
        fresh = ReplayPacer(ctx, again)
        assert fresh.check() is None and fresh.waiting == pacer.waiting
        ctx.clock.advance(BACKOFF.total_seconds())
        await probe_fails(fresh, again, research, "ollama is not running")
        restarted = ReplayPacer(ctx, again)
        assert restarted.check() is None and "(2 back-offs;" in restarted.waiting
        assert all(c.attempts == 0 for c in cells(ctx))
    finally:
        await again.stop()


async def test_a_replay_preempted_just_before_a_restart_is_not_read_as_a_cancel(ctx, runner, research):
    from oracle.services.activity_service import submit_research

    await started(ctx)
    job = ReplayPacer(ctx, runner).check()
    await settle()
    submit_research(ctx, runner, "JPM")
    ReplayPacer(ctx, runner).check()
    await settle()
    assert job.state == "cancelled"
    ReplayPacer(ctx, runner).check()  # a fresh pacer, as after a restart
    assert study_status(ctx).state == "running" and cells(ctx)[0].state == "pending"


def research_key(job):
    symbol, day = job.subject.split("@")
    return symbol, date.fromisoformat(day)


async def drive(ctx, pacer, runner, research, bad, polls=80):
    """Run the study to its end, `bad` listings always failing alike, waiting out each back-off."""
    for _ in range(polls):
        job = pacer.check()
        if study_status(ctx).finished:
            return
        if job is None:
            ctx.clock.advance(LONG_BACKOFF.total_seconds())
            continue
        symbol, day = research_key(job)
        research.finish(symbol, day, "no archive reaches that month" if symbol in bad else None)
        await runner.wait(job.id)
        research.gates.clear()
    raise AssertionError(f"not finished after {polls} polls: {study_status(ctx)}")


async def test_cells_that_always_fail_alike_end_failed_and_the_study_finishes(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=["AAPL", "JPM", "HSBA.L"])
    await drive(ctx, ReplayPacer(ctx, runner), runner, research, bad={"AAPL", "JPM", "HSBA.L"})
    s = study_status(ctx)
    assert (s.finished, s.done, [c.symbol for c in s.failed]) == (True, 0, ["AAPL", "JPM", "HSBA.L"])
    assert all(c.refunds == 2 for c in cells(ctx))


async def test_a_bad_cluster_mid_study_ends_failed_while_the_rest_are_done(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=SYMBOLS[:5])
    await drive(ctx, ReplayPacer(ctx, runner), runner, research, bad={"AAPL", "JPM", "HSBA.L"})
    s = study_status(ctx)
    assert (s.finished, s.done, sorted(c.symbol for c in s.failed)) == (True, 2, ["AAPL", "HSBA.L", "JPM"])


async def test_an_outage_whose_error_changes_still_backs_off_and_charges_little(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 6, 1)
    for symbol in ("AAPL", "JPM", "HSBA.L"):
        await run_one(pacer, runner, research, symbol, day, "ollama is not running")
    begun = ctx.clock.utcnow()
    for n in range(8):
        assert pacer.check() is None and pacer.waiting.startswith("backing off")  # never at polling speed
        ctx.clock.advance(LONG_BACKOFF.total_seconds())
        await probe_fails(pacer, runner, research, f"model {n} reply unusable for {{symbol}}")
    s = study_status(ctx)
    charged = sum(c.attempts for c in cells(ctx))
    assert charged <= 4 and s.failed == () and s.state == "running"
    assert ctx.clock.utcnow() - begun >= 8 * LONG_BACKOFF


async def test_failures_whose_text_varies_still_count_as_one_outage(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    errors = ["no verdict", "timeout after 300s", "bad json from the model", "connection reset", "HTTP 502"]
    for symbol, error in zip(SYMBOLS, errors):
        await run_one(pacer, runner, research, symbol, date(2026, 6, 1), error)
    assert pacer.check() is None and pacer.waiting.startswith("backing off")
    assert all(c.attempts == 0 for c in cells(ctx))


async def test_an_isolated_failure_counts_against_its_own_cell(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 6, 1)
    await run_one(pacer, runner, research, "AAPL", day, "no verdict")
    await run_one(pacer, runner, research, "JPM", day)
    assert pacer.check() is not None
    aapl = ReplayRepo(ctx.conn).cell_at(study_status(ctx).id, "AAPL", day)
    assert (aapl.state, aapl.attempts, aapl.error) == ("pending", 1, "no verdict")


def test_one_outage_reads_the_same_whatever_differs_per_cell():
    from oracle.services.replay_service import _error_kind

    assert _error_kind("unexpected error: ConnectError(<object at 0x7f3a9c>)", "AAPL") == "unexpected error"
    assert (_error_kind("the model's answers were unusable for 'Apple Inc' at 12:03", "AAPL")
            == _error_kind("the model's answers were unusable for 'Sony Group' at 14:55", "6758.T"))
    assert _error_kind("no verdict", "AAPL") != _error_kind("timeout", "AAPL")


async def test_a_hung_replay_is_stopped_and_counted_as_a_failure(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    await settle()
    ctx.clock.advance(44 * 60)
    assert pacer.check() is None and job.state == "running"
    ctx.clock.advance(2 * 60)
    pacer.check()
    await settle()
    assert job.state == "cancelled"
    pacer.check()
    aapl = ReplayRepo(ctx.conn).cell_at(study_status(ctx).id, "AAPL", date(2026, 6, 1))
    assert (aapl.state, aapl.attempts) == ("pending", 1) and "over 45 minutes" in aapl.error
    assert "the least any replay is given" in aapl.error
    assert study_status(ctx).state == "running"


async def test_a_hung_replay_past_three_times_the_longest_recent_one_says_so(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    await settle()
    ctx.clock.advance(20 * 60)
    research.finish("AAPL", date(2026, 6, 1))
    await runner.wait(job.id)
    job = pacer.check()
    await settle()
    ctx.clock.advance(61 * 60)
    pacer.check()
    await settle()
    pacer.check()
    jpm = ReplayRepo(ctx.conn).cell_at(study_status(ctx).id, "JPM", date(2026, 6, 1))
    assert jpm.error == "stopped after running over 60 minutes (3 times the longest recent replay)"


async def test_an_adopted_retry_is_timed_from_when_it_runs_not_when_it_was_queued(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=["AAPL"])
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 8, 3)
    await run_one(pacer, runner, research, "AAPL", day, "model down")
    job = await run_one(pacer, runner, research, "AAPL", day, "bad reply")
    pacer.check()
    [cell] = cells(ctx, "failed")
    again = retry(ctx, runner, job.id)
    await settle()
    # As if adopted while another replay held the queue for 50 minutes.
    ReplayRepo(ctx.conn).adopt(cell.id, again.id, ctx.clock.utcnow() - timedelta(minutes=50))
    ctx.clock.advance(30 * 60)
    pacer.check()
    await settle()
    assert again.state == "running"
    research.finish("AAPL", day)
    await runner.wait(again.id)
    pacer.check()
    assert study_status(ctx).mean_replay_s == 30 * 60


async def test_a_retry_on_a_picked_profile_is_not_taken_for_the_cell(ctx):
    from oracle.services.activity_service import _adopt_replay_cell

    await create_study(ctx, FIRST)
    cell = cells(ctx)[0]
    fail_for_good(ctx, cell)
    _adopt_replay_cell(ctx, SimpleNamespace(id=77, profile="big"), "AAPL", cell.as_of)
    assert ReplayRepo(ctx.conn).cell(cell.id).state == "failed"
    _adopt_replay_cell(ctx, SimpleNamespace(id=77, profile=None), "AAPL", cell.as_of)
    assert ReplayRepo(ctx.conn).cell(cell.id).state == "running"


async def test_create_is_refused_while_the_price_sources_do_not_answer(ctx, yahoo):
    UniverseRepo(ctx.conn).replace_all([*SPREAD, equity("MSFT", "Microsoft", "United States", IT, "NMS", "USD")])
    yahoo.fail["MSFT"] = 503
    with pytest.raises(InvalidQuery, match="did not answer"):
        await create_study(ctx, FIRST, symbols=["MSFT"])


async def test_a_listing_one_source_has_no_data_for_while_another_is_down_is_passed_over(ctx, monkeypatch):
    from oracle.services import replay_service
    from oracle.services.errors import PriceUnavailable

    async def half_answered(c, symbol, since=None):
        raise PriceUnavailable(f"no price data for {symbol} (yahoo: no data; eodhd: HTTP 503)", answered=True,
                               down=("eodhd",))

    monkeypatch.setattr(replay_service, "update_listing", half_answered)
    said = []
    picked, left_out = await pick_listings(ctx, date(2026, 1, 5), said.append)
    assert picked == [] and len(left_out) == 20
    assert any("passed over: no source that answered has data for AAPL (eodhd did not answer)" in s for s in said)

    async def silent(c, symbol, since=None):
        raise PriceUnavailable(f"no price data for {symbol} (yahoo: HTTP 503)", down=("yahoo",))

    monkeypatch.setattr(replay_service, "update_listing", silent)
    with pytest.raises(InvalidQuery, match="did not answer"):
        await pick_listings(ctx, date(2026, 1, 5))


async def test_create_tells_every_slot_it_left_out(ctx):
    s = await create_study(ctx, FIRST)
    assert {"UK Energy", "UK Energy (mid/small)"} <= set(s.left_out) and "US Information Technology" not in s.left_out
    assert len(s.left_out) + len(s.listings) == 40 and s.to_json()["left_out"] == list(s.left_out)


async def test_a_replay_cancelled_in_activity_pauses_the_study_and_is_not_counted(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    job = pacer.check()
    await settle()
    runner.cancel(job.id)
    await settle()
    assert pacer.check() is None
    s = study_status(ctx)
    assert s.state == "paused" and "cancelled in Activity" in s.paused_reason
    assert (cells(ctx)[0].state, cells(ctx)[0].attempts) == ("pending", 0)


async def test_turning_automation_off_stops_the_replay_without_pausing_the_study(ctx, runner, research):
    await started(ctx)
    pacer = ReplayPacer(ctx, runner)
    pacer.check()
    await settle()
    ctx.config = replace(ctx.config, automation=AutomationConfig(enabled=False))
    runner.cancel_automatic()
    await settle()
    pacer.check()
    s = study_status(ctx)
    assert (s.state, s.waiting, cells(ctx)[0].state) == ("running", "automation is off", "pending")


async def test_retrying_a_failed_replay_in_activity_runs_it_for_its_cell(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=["AAPL"])
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 8, 3)
    await run_one(pacer, runner, research, "AAPL", day, "model down")
    job = await run_one(pacer, runner, research, "AAPL", day, "bad reply")
    pacer.check()
    [failed] = cells(ctx, "failed")
    again = retry(ctx, runner, job.id)
    assert ReplayRepo(ctx.conn).cell(failed.id).state == "running"
    assert pacer.check() is None  # it is the cell's replay now: nothing else starts
    research.finish("AAPL", day)
    await runner.wait(again.id)
    pacer.check()
    assert ReplayRepo(ctx.conn).cell(failed.id).state == "done" and study_status(ctx).complete


async def test_status_counts_running_age_and_mean_replay_time(ctx, runner, research):
    await started(ctx)
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
    assert s.mean_replay_s == 120 and (s.finished, s.complete) == (False, False)


# The cutoff and the models, checked at every submit

async def test_the_study_keeps_the_stage_models_it_was_created_with(ctx):
    s = await create_study(ctx, FIRST)
    assert s.stages == {stage: "default" for stage in STAGES}
    assert s.to_json()["stages"]["verdict"] == "default"
    assert s.stage_models == {stage: f"ollama {ctx.config.llm.model}" for stage in STAGES}


async def test_a_profile_renamed_to_the_same_model_does_not_pause_the_study(ctx, runner, research):
    await started(ctx)
    ctx.config = replace(ctx.config, models={"renamed": ModelProfile("ollama", ctx.config.llm.model)},
                         model_slots={stage: "renamed" for stage in STAGES})
    assert ReplayPacer(ctx, runner).check() is not None and study_status(ctx).state == "running"


async def test_a_profile_keeping_its_name_but_naming_another_model_pauses_the_study(ctx, runner, research):
    await started(ctx)
    was = ctx.config.llm.model
    ctx.config = replace(ctx.config, models={"default": ModelProfile("ollama", "another-model")})
    store_probe(ctx.conn, ctx.config)
    assert ReplayPacer(ctx, runner).check() is None and not research.calls
    assert (f"verdict default (ollama {was}) to default (ollama another-model)"
            in study_status(ctx).paused_reason)


async def test_a_study_from_before_models_were_pinned_still_compares_profile_names(ctx, runner, research):
    s = await create_study(ctx, FIRST)
    ctx.conn.execute("UPDATE replay_studies SET stages = ?", (json.dumps({stage: "default" for stage in STAGES}),))
    start_study(ctx)
    assert study_status(ctx).stage_models == {} and study_status(ctx).cutoff == s.cutoff
    ctx.config = replace(ctx.config, models={"renamed": ModelProfile("ollama", ctx.config.llm.model)},
                         model_slots={stage: "renamed" for stage in STAGES})
    assert ReplayPacer(ctx, runner).check() is None
    assert "verdict default to renamed" in study_status(ctx).paused_reason


async def test_the_score_leaves_out_replays_by_the_studys_own_models_cutoff(ctx, runner, research):
    await started(ctx, symbols=["AAPL"])
    await run_one(ReplayPacer(ctx, runner), runner, research, "AAPL", FIRST)
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")},
                         model_slots={"verdict": "big"})
    store_probe(ctx.conn, ctx.config, date(2026, 7, 15), profile="big")
    s = study_status(ctx)
    assert s.cutoff == date(2025, 5, 31) and s.score.excluded == 0 and replay_report(ctx).excluded == 0


async def test_retrying_in_activity_a_study_replay_after_its_models_changed_is_refused(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=["AAPL"])
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 8, 3)
    await run_one(pacer, runner, research, "AAPL", day, "model down")
    job = await run_one(pacer, runner, research, "AAPL", day, "bad reply")
    pacer.check()
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")},
                         model_slots={"verdict": "big"})
    store_probe(ctx.conn, ctx.config, profile="big")
    with pytest.raises(InvalidQuery, match="the replay models changed"):
        retry(ctx, runner, job.id)


async def test_a_slot_pointed_at_an_unprobed_profile_after_create_pauses_the_study(ctx, runner, research):
    await started(ctx)
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")},
                         model_slots={"verdict": "big"})
    assert ReplayPacer(ctx, runner).check() is None and not research.calls
    s = study_status(ctx)
    assert s.state == "paused"
    assert ("the replay models changed since the study was created: verdict default (ollama "
            f"{ctx.config.llm.model}) to big (ollama big-model)") in s.paused_reason
    with pytest.raises(InvalidQuery, match="the replay models changed"):
        start_study(ctx)


async def test_a_stage_model_changed_after_create_pauses_the_study_even_when_probed(ctx, runner, research):
    await started(ctx)
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")},
                         model_slots={"notes": "big"})
    store_probe(ctx.conn, ctx.config, profile="big")
    assert ReplayPacer(ctx, runner).check() is None and not research.calls
    assert "notes default (ollama " in study_status(ctx).paused_reason


async def test_a_stage_model_no_longer_probed_pauses_the_study_saying_what_to_probe(ctx, runner, research):
    await started(ctx)
    ctx.conn.execute("DELETE FROM cutoff_probes")  # as if never probed
    assert ReplayPacer(ctx, runner).check() is None and not research.calls
    s = study_status(ctx)
    assert s.state == "paused" and "probe default first" in s.paused_reason
    with pytest.raises(InvalidQuery, match="probe default first"):
        start_study(ctx)


async def test_a_later_cutoff_stops_earlier_cells_and_drops_earlier_replays_from_the_score(ctx, runner, research):
    await started(ctx, symbols=["AAPL", "JPM"])
    pacer = ReplayPacer(ctx, runner)
    await run_one(pacer, runner, research, "AAPL", FIRST)
    store_probe(ctx.conn, ctx.config, date(2026, 7, 15))  # a re-probe found the model knows more
    job = pacer.check()
    assert job.subject == "AAPL@2026-08-03"
    s = study_status(ctx)
    assert s.cutoff == date(2026, 7, 15)
    assert [(c.symbol, c.as_of) for c in s.failed] == [
        ("JPM", date(2026, 6, 1)), ("AAPL", date(2026, 7, 1)), ("JPM", date(2026, 7, 1))]
    assert all(c.error == "on or before the cutoff 2026-07-15 of default" for c in s.failed)
    assert s.score.excluded == 1 and replay_report(ctx).excluded == 1
    assert retry_failed(ctx) == 0
    with pytest.raises(InvalidQuery, match="on or before the measured cutoff 2026-07-15"):
        retry_failed(ctx, s.failed[0].id)


async def test_a_raised_claim_stops_earlier_cells_too(ctx, runner, research):
    await started(ctx, symbols=["AAPL"])
    ctx.config = replace(ctx.config, models={"default": ModelProfile("ollama", ctx.config.llm.model,
                                                                     claimed_cutoff="2026-07-31")})
    assert ReplayPacer(ctx, runner).check().subject == "AAPL@2026-08-03"
    assert [c.as_of for c in study_status(ctx).failed] == [date(2026, 6, 1), date(2026, 7, 1)]


def stopped_and_left_out(ctx):
    """The cells the pacer stopped for the cutoff, and the cells the score leaves out for it."""
    s = study_status(ctx)
    stopped = {(c.symbol, c.as_of) for c in s.failed if c.error.startswith("on or before the cutoff")}
    return stopped, {(c.symbol, c.as_of) for c in cells(ctx) if s.cutoff and c.as_of <= s.cutoff}


async def test_a_claim_on_another_profile_of_the_pinned_model_moves_neither_pacer_nor_score(ctx, runner, research):
    await started(ctx, symbols=["AAPL"])
    model = ctx.config.llm.model
    ctx.config = replace(ctx.config, models={"default": ModelProfile("ollama", model),
                                             "other": ModelProfile("ollama", model, claimed_cutoff="2026-07-31")})
    pacer = ReplayPacer(ctx, runner)
    await run_one(pacer, runner, research, "AAPL", FIRST)
    assert stopped_and_left_out(ctx) == (set(), set())
    assert study_status(ctx).score.excluded == 0


async def test_a_raised_claim_on_the_pinned_profile_moves_pacer_and_score_together(ctx, runner, research):
    await started(ctx, symbols=["AAPL", "JPM"])
    model = ctx.config.llm.model
    ctx.config = replace(ctx.config, models={"default": ModelProfile("ollama", model, claimed_cutoff="2026-06-15"),
                                             "other": ModelProfile("ollama", model, claimed_cutoff="2026-07-31")})
    pacer = ReplayPacer(ctx, runner)
    assert pacer.check().subject == "AAPL@2026-07-01"
    stopped, left_out = stopped_and_left_out(ctx)
    assert stopped == left_out == {("AAPL", FIRST), ("JPM", FIRST)}


async def test_activity_retry_counts_the_claim_of_the_profile_the_slots_now_run_on(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=["AAPL"])
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 8, 3)
    await run_one(pacer, runner, research, "AAPL", day, "model down")
    job = await run_one(pacer, runner, research, "AAPL", day, "bad reply")
    model = ctx.config.llm.model
    # The slots move to another profile of the same model claiming more: the run now uses that claim, so it counts.
    ctx.config = replace(ctx.config, models={"default": ModelProfile("ollama", model),
                                             "other": ModelProfile("ollama", model, claimed_cutoff="2026-08-10")},
                         model_slots={stage: "other" for stage in STAGES})
    assert study_status(ctx).cutoff == date(2026, 8, 10)
    with pytest.raises(InvalidQuery, match="on or before the measured cutoff 2026-08-10"):
        retry(ctx, runner, job.id)


async def test_the_pacer_names_the_profile_whose_claim_set_the_cutoff(ctx, runner, research):
    await started(ctx, symbols=["AAPL"])
    model = ctx.config.llm.model
    ctx.config = replace(ctx.config, models={"default": ModelProfile("ollama", model, claimed_cutoff="2026-06-15"),
                                             "other": ModelProfile("ollama", model)},
                         model_slots={stage: "other" for stage in STAGES})
    assert ReplayPacer(ctx, runner).check().subject == "AAPL@2026-07-01"
    assert [c.error for c in study_status(ctx).failed] == ["on or before the cutoff 2026-06-15 of default"]


async def test_replace_after_the_cutoff_moved_makes_no_cell_on_or_before_it(ctx):
    await create_study(ctx, FIRST, symbols=["AAPL", "JPM"])
    store_probe(ctx.conn, ctx.config, date(2026, 7, 15))
    s = await replace_listing(ctx, "AAPL", "SAP.DE")
    assert s.first_day > date(2026, 7, 15) and min(c.as_of for c in cells(ctx)) > date(2026, 7, 15)


async def test_retrying_in_activity_a_replay_now_on_or_before_the_cutoff_is_refused(ctx, runner, research):
    await started(ctx, date(2026, 8, 1), symbols=["AAPL"])
    pacer = ReplayPacer(ctx, runner)
    day = date(2026, 8, 3)
    await run_one(pacer, runner, research, "AAPL", day, "model down")
    job = await run_one(pacer, runner, research, "AAPL", day, "bad reply")
    pacer.check()
    store_probe(ctx.conn, ctx.config, date(2026, 8, 10))
    with pytest.raises(InvalidQuery, match="on or before the measured cutoff 2026-08-10"):
        retry(ctx, runner, job.id)
    assert not [r for r in retryable(ctx, runner) if r.id == job.id]


# The size mix

SMALLER = [
    equity("MIDT", "Mid Tech", "United States", IT, "NMS", "USD", cap="Mid Cap"),
    equity("SMLT", "Small Tech", "United States", IT, "NMS", "USD", cap="Small Cap"),
    equity("MICT", "Micro Tech", "United States", IT, "NMS", "USD", cap="Micro Cap"),
    equity("SMLF", "Small Bank", "United States", FIN, "NYQ", "USD", cap="Small Cap"),
    equity("NANF", "Nano Bank", "United States", FIN, "NYQ", "USD", cap="Nano Cap"),
    equity("TSLA", "Tesla, Inc.", "Japan", IT, "JPX", "JPY", cap="Mid Cap"),
    equity("MIDJ.DE", "Mid Japan", "Japan", IT, "GER", "EUR", cap="Mid Cap"),
    equity("SMLH.L", "Small Health", "United Kingdom", "Health Care", "LSE", "GBp", cap="Small Cap"),
]


@pytest.fixture
def mixed(ctx, yahoo):
    UniverseRepo(ctx.conn).replace_all([*SPREAD, *SMALLER])
    for x in SMALLER:
        priced(yahoo, x.symbol)
    return ctx


async def test_the_mix_adds_one_mid_cap_per_slot_else_a_small_cap_never_micro_or_nano(mixed):
    picked, left_out = await pick_listings(mixed, FIRST, bands="mix")
    assert [p.symbol for p in picked] == [*SYMBOLS, "MIDT", "SMLF", "SMLH.L"]
    assert [(p.region, p.sector, p.market_cap) for p in picked[-3:]] == [
        ("US", IT, "Mid Cap"), ("US", FIN, "Small Cap"), ("UK", "Health Care", "Small Cap")]
    # Tesla is never picked, and a Frankfurt line is not a Japanese company's home line.
    assert "Japan Information Technology (mid/small)" in left_out
    assert "US Information Technology (mid/small)" not in left_out and len(left_out) == 13 + 17


async def test_the_large_pick_is_unchanged_and_mid_small_takes_only_the_smaller_half(mixed):
    assert [p.symbol for p in (await pick_listings(mixed, FIRST, bands="large"))[0]] == SYMBOLS
    assert [p.symbol for p in (await pick_listings(mixed, FIRST))[0]] == SYMBOLS
    assert [p.symbol for p in (await pick_listings(mixed, FIRST, bands="mid-small"))[0]] == ["MIDT", "SMLF", "SMLH.L"]


async def test_symbols_stand_in_for_the_large_half_of_a_mix(mixed):
    s = await create_study(mixed, FIRST, symbols=["JPM", "6758.T"], bands="mix")
    assert [p.symbol for p in s.listings] == ["JPM", "6758.T", "MIDT", "SMLF", "SMLH.L"]
    assert s.total == 3 * 5
    s = await create_study(mixed, FIRST, symbols=["JPM"], bands="large")
    assert [p.symbol for p in s.listings] == ["JPM"]
    with pytest.raises(InvalidQuery, match="large half"):
        await create_study(mixed, FIRST, symbols=["JPM"], bands="mid-small")
    with pytest.raises(InvalidQuery, match="large, mid-small or mix"):
        await create_study(mixed, FIRST, bands="tiny")


async def test_a_new_study_is_a_mix_unless_told_otherwise(mixed):
    s = await create_study(mixed, FIRST)
    assert [p.symbol for p in s.listings] == [*SYMBOLS, "MIDT", "SMLF", "SMLH.L"]


async def test_a_pick_keeps_the_band_it_was_ranked_by_the_companys_primary_one(mixed, yahoo):
    UniverseRepo(mixed.conn).replace_all([
        *SPREAD, equity("SMLJ.DE", "Small Japan", "Japan", IT, "GER", "EUR", cap="Mid Cap", key="smlj"),
        equity("6666.T", "Small Japan", "Japan", IT, "JPX", "JPY", cap="Small Cap", key="smlj", primary=False)])
    priced(yahoo, "6666.T")
    picked, _ = await pick_listings(mixed, FIRST, bands="mid-small")
    assert [(p.symbol, p.market_cap, p.half) for p in picked] == [("6666.T", "Mid Cap", "mid-small")]


async def test_a_named_listing_without_a_band_takes_its_primarys_else_counts_in_the_large_half(mixed, yahoo):
    UniverseRepo(mixed.conn).replace_all([
        *SPREAD, *SMALLER,
        equity("BANDX", "Banded Co", "Japan", IT, "NYQ", "USD", cap="Large Cap", key="banded"),
        equity("7777.T", "Banded Co", "Japan", IT, "JPX", "JPY", cap=None, key="banded", primary=False),
        equity("8888.T", "Unbanded Co", "Japan", IT, "JPX", "JPY", cap=None)])
    for symbol in ("7777.T", "8888.T"):
        priced(yahoo, symbol)
    s = await create_study(mixed, FIRST, symbols=["7777.T", "8888.T"])
    assert [(p.symbol, p.market_cap, p.half) for p in s.listings[:2]] == [
        ("7777.T", "Large Cap", "large"), ("8888.T", None, "large")]
    sizes = {z.size: z.listings for z in replay_report(mixed).by_size}
    assert sizes == {"large": ("7777.T", "8888.T"), "mid-small": ("MIDT", "SMLF", "SMLH.L")}
