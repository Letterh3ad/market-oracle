"""The signal testbed: point-in-time S&P 500 membership, bars and sectors in the research
database only, resumable and yielding, and the missing share per year."""

from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config
from oracle.market.yahoo import YahooPrices
from oracle.services.activity_service import JobRunner, retry, submit_testbed
from oracle.services.context import open_context
from oracle.services.testbed_service import (
    FAILED_IN_A_ROW, TAIL, build_testbed, last_run, open_testbed, testbed_coverage as coverage_of,
)
from oracle.services.testbed_service import REUSED, research_context
from oracle.store.testbed import TestbedRepo
from tests.fakes.clock import FakeClock
from tests.fakes.sp500 import SHA, FakeSp500
from tests.fakes.web import FakeWeb
from tests.fakes.wikipedia import FakeWikipedia
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2012, 12, 31, 12, 0, tzinfo=timezone.utc)
NY = "America/New_York"
ROWS = [
    ("2005-01-03", "AAA,OLD"),
    ("2009-12-01", "AAA,BBB,BRK.B,DDD"),
    ("2011-03-07", "AAA,BRK.B,CCC,DDD"),
    ("2012-06-04", "AAA,BRK.B,CCC"),
]
SECTORS = ("<table class='wikitable'><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>"
           "<tr><td>AAA</td><td>Aaa Inc</td><td>Industrials</td></tr>"
           "<tr><td>BRK.B</td><td>Berkshire</td><td>Financials</td></tr>"
           "<tr><td>CCC</td><td>Ccc Corp</td><td>Energy</td></tr>"
           "<tr><td>ZZZ</td><td>Never a member here</td><td>Utilities</td></tr></table>")


def weekdays(start: str, end: str) -> list[date]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


def series(start: str, end: str = "2012-12-28") -> list[tuple]:
    return [bar(d.isoformat(), 10.0) for d in weekdays(start, end)]


@pytest.fixture
def env(tmp_path):
    yahoo, wiki, gh = FakeYahoo(), FakeWikipedia(), FakeSp500(ROWS)
    wiki.pages["List_of_S%26P_500_companies"] = SECTORS
    for s in ("SPY", "ACWI", "AAA", "BRK-B"):
        yahoo.add(s, "USD", NY, series("2009-06-01"))
    yahoo.add("BBB", "USD", NY, series("2010-01-01"))
    yahoo.add("CCC", "USD", NY, series("2011-01-03"))
    yahoo.summaries["BBB"] = {"assetProfile": {"sector": "Technology"}}
    web = FakeWeb(yahoo, wiki, gh)
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW), web.transport) as ctx:
        yield ctx, yahoo, gh, web


def members(ctx):
    with research_context(ctx) as r:
        return TestbedRepo(r.conn).members()


def dump(conn) -> list[str]:
    return list(conn.iterdump())


def charts(web) -> list[str]:
    return [r.url.path.rsplit("/", 1)[1] for r in web.requests_to("query2.finance.yahoo.com")
            if "/chart/" in r.url.path]


async def test_the_build_writes_only_the_research_database(env):
    ctx, *_ = env
    before = dump(ctx.conn)
    run = await build_testbed(ctx)
    assert run.stopped is None and not run.failures, run.summary
    assert dump(ctx.conn) == before, "the live database is untouched: no bars, no pacing, no lock"
    assert ctx.config.research_db_path.exists()


async def test_membership_on_a_day_is_as_of_that_day(env):
    ctx, *_ = env
    await build_testbed(ctx)
    with open_testbed(ctx) as tb:
        assert tb.members_on(date(2011, 3, 4)) == {"AAA", "BBB", "BRK-B", "DDD"}
        assert tb.members_on(date(2011, 3, 7)) == {"AAA", "BRK-B", "CCC", "DDD"}, "added and removed that day"
        assert tb.members_on(date(2012, 6, 4)) == {"AAA", "BRK-B", "CCC"}
        assert tb.members_on(date(2004, 12, 31)) == frozenset()
        snap = tb.snapshot()
        assert (snap.revision, snap.first_day, snap.last_day, snap.changes) == (SHA, date(2005, 1, 3),
                                                                                 date(2012, 6, 4), 4)
        assert snap.fetched_at.startswith("2012-12-31")


async def test_bars_for_every_member_ever_from_the_start_and_none_after_a_members_tail(env):
    ctx, _, _, web = env
    await build_testbed(ctx)
    assert "OLD" not in charts(web), "a member only before the start is out of scope"
    with open_testbed(ctx) as tb:
        aaa = tb.bars("AAA", date(2000, 1, 1))
        assert aaa[0].date == date(2010, 1, 1)
        bbb = tb.bars("BBB", date(2000, 1, 1))
        assert bbb[-1].date <= date(2011, 3, 7) + TAIL, "a ticker's bars after its exit may be another company's"
        assert tb.bars("SPY", date(2009, 1, 1))[0].date == date(2010, 1, 1)
        assert tb.bars("DDD", date(2000, 1, 1)) == []
        assert tb.calendar(date(2010, 1, 2), date(2010, 1, 8)) == weekdays("2010-01-04", "2010-01-08")


async def test_sectors_from_wikipedia_for_members_and_from_yahoo_in_gics_words_for_former_ones(env):
    ctx, yahoo, *_ = env
    run = await build_testbed(ctx)
    with open_testbed(ctx) as tb:
        assert tb.sectors() == {"AAA": "Industrials", "BRK-B": "Financials", "CCC": "Energy",
                                "BBB": "Information Technology", "DDD": None, "OLD": None}
    assert yahoo.profiled == ["BBB"], "only a priced member Wikipedia lacks is asked"
    assert run.sectors_set == 4


async def test_coverage_counts_member_days_with_and_without_bars_per_year_and_lists_the_unpriced(env):
    ctx, *_ = env
    await build_testbed(ctx)
    c = coverage_of(ctx)
    days = {y: len(weekdays(f"{y}-01-01", f"{y}-12-31" if y < 2012 else "2012-12-28")) for y in (2010, 2011, 2012)}
    ddd_2012 = len(weekdays("2012-01-01", "2012-06-01"))
    by_year = {y.year: y for y in c.years}
    assert (by_year[2010].member_days, by_year[2010].with_bars, by_year[2010].unpriced) == (4 * days[2010],
                                                                                            3 * days[2010], 1)
    assert by_year[2010].missing_share == 0.25
    assert by_year[2011].member_days == 4 * days[2011] and by_year[2011].members == 5
    assert (by_year[2012].member_days, by_year[2012].with_bars) == (3 * days[2012] + ddd_2012, 3 * days[2012])
    [ddd] = c.under_priced
    assert c.duplicates == () and c.as_of == NOW.date()
    assert (ddd.ticker, ddd.note, ddd.first_day, ddd.last_day) == ("DDD", "not on Yahoo", date(2010, 1, 1),
                                                                   date(2012, 6, 1))
    assert c.calendar == "SPY" and c.members == 5 and c.fetched == 5 and c.sectors == 4
    assert 0 < c.missing_share < 0.25
    assert c.last_run is not None and c.snapshot is not None


async def test_coverage_before_any_build_is_empty_not_an_error(env):
    ctx, *_ = env
    c = coverage_of(ctx)
    assert c.years == () and c.snapshot is None and c.last_run is None and c.missing_share == 0.0


async def test_a_second_run_asks_nothing_again_and_refresh_tops_up_current_members(env):
    ctx, _, gh, web = env
    await build_testbed(ctx)
    asked = len(web.requests)
    again = await build_testbed(ctx)
    assert len(web.requests) == asked and again.requests == 0, "resumable: what is stored is not asked again"
    await build_testbed(ctx, refresh=True)
    topped = charts(web)[charts(web).index("ACWI", 2):]
    assert gh.reads == 2
    assert set(topped) >= {"AAA", "BRK-B", "CCC"} and "BBB" not in topped and "DDD" not in topped
    with open_testbed(ctx) as tb:
        assert tb.bars("AAA", date(2012, 12, 1))[-1].date == date(2012, 12, 28)
    assert {m.ticker: m.bars_note for m in members(ctx)}["AAA"] is None, "nothing newer yet is not 'no bars'"
    asked = len(charts(web))
    await build_testbed(ctx, refresh=True)
    assert {"AAA", "BRK-B", "CCC"} <= set(charts(web)[asked:]), "a refresh before the close never freezes a member"


async def test_a_cancelled_run_stops_between_requests_and_the_next_resumes(env):
    ctx, _, _, web = env
    calls = []

    def stop():
        calls.append(1)
        return len(calls) > 5

    run = await build_testbed(ctx, stop=stop)
    assert run.stopped == "cancelled"
    first = charts(web)
    await build_testbed(ctx)
    rest = charts(web)[len(first):]
    assert not set(first) & set(rest) - {"SPY", "ACWI"}, "a member tried is not asked again"
    assert coverage_of(ctx).fetched == 5


async def test_it_waits_while_live_work_runs(env):
    ctx, *_ = env
    answers = ["the scheduled tick at 22:00 UTC", "the scheduled tick at 22:00 UTC"]
    said = []
    run = await build_testbed(ctx, busy=lambda: answers.pop() if answers else None, on_progress=said.append)
    assert run.stopped is None
    assert said[:2] == ["waiting for the scheduled tick at 22:00 UTC"] * 2
    assert ctx.clock.sleeps[:2] == [5.0, 5.0]


async def test_failures_in_a_row_stop_the_run_and_leave_members_untried(env):
    ctx, yahoo, *_ = env
    for s in ("AAA", "BBB", "BRK-B", "CCC", "DDD"):
        yahoo.fail[s] = 503
    run = await build_testbed(ctx)
    assert run.stopped and str(FAILED_IN_A_ROW) in run.stopped
    assert coverage_of(ctx).fetched == 0


async def test_only_and_limit_narrow_a_smoke_run_and_end_bounds_the_bars(env):
    ctx, _, _, web = env
    run = await build_testbed(ctx, limit=2, end=date(2010, 12, 31))
    assert run.members == 2
    assert [s for s in charts(web) if s not in ("SPY", "ACWI")] == ["AAA", "BBB"]
    with open_testbed(ctx) as tb:
        assert tb.bars("AAA", date(2010, 1, 1))[-1].date == date(2010, 12, 31)
    assert coverage_of(ctx).fetched == 0, "a run over part of the period marks no member tried"
    await build_testbed(ctx, limit=1)
    with open_testbed(ctx) as tb:
        assert tb.bars("AAA", date(2010, 1, 1))[-1].date == date(2012, 12, 28), "a full build extends it"
    run = await build_testbed(ctx, only=["brk.b"])
    assert run.members == 1 and charts(web)[-1] == "BRK-B"


async def test_an_unreadable_history_stops_a_first_build(env):
    ctx, _, gh, _ = env
    gh.status = 500
    run = await build_testbed(ctx)
    assert run.stopped and "membership" in run.stopped
    assert last_run(ctx).stopped == run.stopped


async def test_the_build_is_one_activity_job_and_retry_resumes_a_cancelled_one(env):
    ctx, *_ = env
    runner = JobRunner(ctx)
    job = submit_testbed(ctx, runner)
    assert submit_testbed(ctx, runner) is job, "pressed twice joins the first"
    runner.cancel(job.id)
    again = await runner.wait(retry(ctx, runner, job.id).id)
    assert again.state == "done", again.error
    assert coverage_of(ctx).fetched == 5


@pytest.mark.parametrize("currency, kind", [(None, "MUTUALFUND"), ("USD", "ETF")])
async def test_a_former_tickers_answer_that_is_not_the_companys_shares_is_noted_and_the_run_goes_on(env, currency,
                                                                                                    kind):
    ctx, yahoo, *_ = env
    yahoo.add("BBB", currency, NY, series("2010-01-01"), kind=kind)
    run = await build_testbed(ctx)
    assert run.stopped is None and not run.failures and run.reused == 1
    note = {m.ticker: m.bars_note for m in members(ctx)}["BBB"]
    assert note.startswith(REUSED) and kind in note
    with open_testbed(ctx) as tb:
        assert tb.bars("BBB", date(2000, 1, 1)) == []


async def test_a_former_tickers_bars_from_a_listing_that_began_after_it_joined_are_another_companys(env):
    ctx, yahoo, *_ = env
    yahoo.add("BBB", "USD", NY, series("2010-09-20"), first_trade=date(2010, 9, 20))
    yahoo.add("CCC", "USD", NY, series("2012-01-02"), first_trade=date(2012, 1, 2))
    await build_testbed(ctx)
    notes = {m.ticker: m.bars_note for m in members(ctx)}
    assert notes["BBB"] == f"{REUSED}: Yahoo's BBB first traded 2010-09-20, after it joined the index"
    assert notes["CCC"] is None, "a current member's ticker is its own, whenever Yahoo's history starts"
    with open_testbed(ctx) as tb:
        assert tb.bars("BBB", date(2000, 1, 1)) == [] and tb.bars("CCC", date(2000, 1, 1))


async def test_one_tickers_unexpected_answer_is_its_failure_and_the_run_goes_on(env, monkeypatch):
    ctx, *_ = env
    real = YahooPrices.daily_chart

    async def chart(self, instrument, since, now):
        if instrument.symbol == "BBB":
            raise KeyError("chart")
        return await real(self, instrument, since, now)

    monkeypatch.setattr(YahooPrices, "daily_chart", chart)
    run = await build_testbed(ctx)
    assert run.stopped is None and run.failures == ["BBB bars: KeyError: 'chart'"]
    assert {m.ticker: m.bars_tried_at for m in members(ctx)}["BBB"] is None, "asked again next run"
    assert run.priced == 3


async def test_the_last_run_is_kept_when_the_build_raises(env, monkeypatch):
    ctx, *_ = env

    def boom(self, only, limit):
        raise RuntimeError("disk full")

    monkeypatch.setattr("oracle.services.testbed_service._Build.scope", boom)
    with pytest.raises(RuntimeError):
        await build_testbed(ctx)
    assert last_run(ctx).stopped == "failed: RuntimeError: disk full"


async def test_members_priced_on_under_half_their_days_are_listed_with_counts(env):
    ctx, yahoo, *_ = env
    yahoo.add("AAA", "USD", NY, series("2012-06-01"))
    await build_testbed(ctx)
    under = {u.ticker: u for u in coverage_of(ctx).under_priced}
    assert set(under) == {"AAA", "DDD"}
    aaa = under["AAA"]
    assert aaa.with_bars == len(weekdays("2012-06-01", "2012-12-28"))
    assert aaa.note == f"priced on {aaa.with_bars} of {aaa.member_days} member-days"


async def test_one_company_listed_under_two_tickers_on_the_same_days_is_flagged(env):
    ctx, _, gh, _ = env
    gh.rows = [*ROWS[:3], ("2012-01-03", "AAA,BRK.B,CCC,DDD,KORS,CPRI"), ("2012-06-04", "AAA,BRK.B,CCC,CPRI")]
    await build_testbed(ctx)
    [d] = coverage_of(ctx).duplicates
    assert (d.tickers, d.first_day, d.last_day, d.days) == (("KORS", "CPRI"), date(2012, 1, 3), date(2012, 6, 1),
                                                            len(weekdays("2012-01-03", "2012-06-01")))
