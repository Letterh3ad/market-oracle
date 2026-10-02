"""The trend overlay test: ^SP500TR fetched into the research database only, choose 1988-2007
reported, confirm 2008 on judged as pre-registered, and the result kept."""

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.broker.trend_overlay import judge
from oracle.config import Config
from oracle.services.activity_service import JobRunner, retry, submit_plan_trend
from oracle.services.context import open_context
from oracle.services.errors import NoPrices
from oracle.services.testbed_service import research_context
from oracle.services.trend_service import (CASH_NONE, CHOOSE_END, SERIES, TBILL, run_and_keep_trend, run_trend,
                                           trend_tests)
from oracle.market.bars import Bar
from oracle.store.bars import BarRepo
from tests.fakes.clock import FakeClock
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2013, 1, 1, 12, 0, tzinfo=timezone.utc)
NY = "America/New_York"


def weekdays(a: date, b: date) -> list[date]:
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


def level(d: date) -> float:
    """A steady rise with a deep 2008 fall and a 1990 dip, so both windows switch."""
    t = (d - date(1988, 1, 1)).days / 365.25
    v = 100 * math.exp(0.08 * t)
    if date(2008, 6, 1) <= d <= date(2009, 3, 1):
        v *= 1 - 0.5 * (d - date(2008, 6, 1)).days / 273
    elif d > date(2009, 3, 1):
        v *= 0.5 + 0.5 * min(1.0, (d - date(2009, 3, 1)).days / 900)
    if date(1990, 7, 1) <= d <= date(1990, 12, 31):
        v *= 0.8
    return v


def bars(days: list[date]) -> list[tuple]:
    return [(d, level(d) * 0.999, level(d), level(d), level(d), level(d), 0) for d in days]


@pytest.fixture
def env(tmp_path):
    yahoo = FakeYahoo()
    yahoo.add(SERIES.symbol, "USD", NY, bars(weekdays(date(1988, 1, 4), date(2012, 12, 31))), kind="INDEX")
    web = FakeWeb(yahoo)
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW), web.transport) as ctx:
        yield ctx, web


async def test_the_run_fetches_into_the_research_database_only_and_keeps_the_result(env):
    ctx, web = env
    before = list(ctx.conn.iterdump())
    t = await run_and_keep_trend(ctx)
    assert list(ctx.conn.iterdump()) == before, "the live database is untouched"
    with research_context(ctx) as r:
        assert BarRepo(r.conn).last_date(SERIES.symbol) == date(2012, 12, 31)
    assert t.series == SERIES.symbol and t.cash == CASH_NONE and t.beside == ()
    assert (t.choose.start, t.choose.end) == (date(1988, 1, 4), date(2007, 12, 31))
    assert (t.confirm.start, t.confirm.end) == (date(2008, 1, 1), date(2012, 12, 31))
    assert t.choose.verdict is None, "choose is reported only"
    trend, held = t.confirm.trend, t.confirm.held
    j = judge(trend.max_fall, held.max_fall, trend.cagr, held.cagr)
    assert t.confirm.verdict == ("PASS" if j.passed else "FAIL") and t.passed == j.passed
    assert t.confirm.switches > 0 and 0 < trend.cash_share < 1
    assert [c.name for c in t.confirm.crashes] == ["2008"]
    assert "10-month" in t.rule and t.cost == 0.001
    kept = trend_tests(ctx)
    assert kept is not None and kept.id and kept.confirm == t.confirm and kept.choose == t.choose


async def test_a_second_run_asks_again_from_the_last_stored_day_so_a_part_day_is_replaced(env):
    ctx, web = env
    await run_and_keep_trend(ctx)
    await run_and_keep_trend(ctx)
    asked = [r for r in web.requests if "/chart/" in r.url.path]
    assert len(asked) == 2
    last = datetime(2012, 12, 31, tzinfo=timezone.utc).timestamp()
    assert int(asked[1].url.params["period1"]) == int(last)


async def test_the_run_days_session_is_left_out_as_its_close_may_not_be_final(tmp_path):
    yahoo = FakeYahoo()
    yahoo.add(SERIES.symbol, "USD", NY, bars(weekdays(date(1988, 1, 4), date(2013, 1, 2))), kind="INDEX")
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(datetime(2013, 1, 2, 18, 0, tzinfo=timezone.utc)),
                      FakeWeb(yahoo).transport) as ctx:
        t = await run_and_keep_trend(ctx)
    assert t.confirm.end == date(2013, 1, 1) and t.last_day == date(2013, 1, 1)


async def test_the_choose_window_reads_no_session_after_2007(env, monkeypatch):
    ctx, _ = env
    await run_and_keep_trend(ctx)
    import oracle.services.trend_service as svc
    seen = []
    real = svc.simulate

    def spy(sessions, start, end, **kw):
        seen.append((end, max(s.day for s in sessions)))
        return real(sessions, start, end, **kw)

    monkeypatch.setattr(svc, "simulate", spy)
    run_trend(ctx)
    assert seen[0] == (CHOOSE_END, date(2007, 12, 31))


async def test_a_stored_t_bill_series_pays_cash_and_is_named(env):
    ctx, _ = env
    with research_context(ctx) as r:
        BarRepo(r.conn).upsert([Bar(TBILL, d, None, None, None, 4.0, None, None, "USD", "yahoo")
                                for d in weekdays(date(1988, 1, 4), date(2012, 12, 31))], "x")
    t = await run_and_keep_trend(ctx)
    assert TBILL in t.cash and t.cash != CASH_NONE


def test_no_stored_series_says_so(env):
    ctx, _ = env
    with pytest.raises(NoPrices):
        run_trend(ctx)


def test_nothing_kept_yet_is_none(env):
    assert trend_tests(env[0]) is None


async def test_notes_count_opens_equal_to_the_close_and_to_the_previous_close(tmp_path):
    days = weekdays(date(1988, 1, 4), date(2012, 12, 31))
    rows = []
    for i, d in enumerate(days):
        o = level(d) if d.year < 2000 else level(days[i - 1]) if d.year == 2010 else level(d) * 0.999
        rows.append((d, o, level(d), level(d), level(d), level(d), 0))
    yahoo = FakeYahoo()
    yahoo.add(SERIES.symbol, "USD", NY, rows, kind="INDEX")
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW), FakeWeb(yahoo).transport) as ctx:
        t = await run_and_keep_trend(ctx)
    same = sum(d.year < 2000 for d in days)
    prev = sum(d.year == 2010 for d in days)
    assert any(n.startswith(f"{same} sessions (1988-01-04 to 1999-12-31) have no real open") for n in t.notes)
    assert any(n.startswith(f"{prev} sessions (2010-01-01 to 2010-12-31) open exactly at the previous close")
               for n in t.notes)


async def test_a_failed_update_is_a_note_when_days_are_stored(env):
    ctx, web = env
    await run_and_keep_trend(ctx)
    web.services[0].fail[SERIES.symbol] = 500
    t = await run_and_keep_trend(ctx)
    assert t.notes[-1].startswith("Could not update ^SP500TR") and t.confirm.end == date(2012, 12, 31)


async def test_the_job_runs_keeps_and_can_be_retried(env):
    ctx, web = env
    runner = JobRunner(ctx)
    web.services[0].fail[SERIES.symbol] = 500
    failed = await runner.wait(submit_plan_trend(ctx, runner).id)
    assert failed.state == "failed" and trend_tests(ctx) is None
    del web.services[0].fail[SERIES.symbol]
    again = await runner.wait(retry(ctx, runner, failed.id).id)
    assert again.state == "done" and trend_tests(ctx) is not None
