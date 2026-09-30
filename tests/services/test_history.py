"""The history backfill: news, filings and price bars back in time for the main list and each
one's industry map, resumable and paced, and the coverage it reports."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import AnalystsConfig, Config
from oracle.market.bars import Bar
from oracle.services.activity_service import JobRunner, retry, submit_history
from oracle.services.context import open_context
from oracle.services.history_service import (
    GOOGLE_CAP, backfill_history, company_history, coverage, history_companies, news_query, news_windows, search_name,
)
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.bars import BarRepo
from oracle.store.history import HistoryRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity, etf
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.sec import FakeSec
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2023, 1, 15, 12, 0, tzinfo=timezone.utc)
START = date(2022, 9, 1)
Q3, Q4 = (date(2022, 9, 1), date(2022, 10, 1)), (date(2022, 10, 1), date(2023, 1, 1))
AUTOS = dict(sector="Consumer Discretionary", industry_group="Automobiles & Components", industry="Automobiles")


def car(symbol, name, cap, **extra):
    return equity(symbol, name, market_cap=cap, **{"currency": "USD", **AUTOS, **extra})


ROWS = [
    car("HMC", "Honda Motor Co.", "Large Cap"),
    car("GM", "General Motors", "Large Cap"),
    car("TSLA", "Tesla, Inc.", "Mega Cap"),
    car("7201.T", "Nissan Motor Co., Ltd.", "Mid Cap", country="Japan", exchange="JPX", currency="JPY"),
]


def when(y, m, d):
    return datetime(y, m, d, 9, 0, tzinfo=timezone.utc)


def bars(symbol, first: date, n=3):
    return [(date.fromordinal(first.toordinal() + i), 1, 1, 1, 10.0 + i, 10.0 + i, 100) for i in range(n)]


@pytest.fixture
async def env(tmp_path):
    news, sec, yahoo = FakeGoogleNews(), FakeSec(), FakeYahoo()
    web = FakeWeb(FakeFinanceDatabase(ROWS, [etf("CARZ", "Global Auto ETF")]), yahoo, news, sec)
    config = Config(db_path=tmp_path / "o.db", analysts=AnalystsConfig(peers=5))
    config = replace(config, research=replace(config.research, contact="Faye faye@example.com"))
    manager = open_context(config, None, FakeClock(NOW), web.transport)
    ctx = manager.__enter__()
    await refresh_universe(ctx)
    watch(ctx, "HMC")
    sec.companies = {"HMC": (715153, "HONDA MOTOR CO LTD"), "GM": (1467858, "General Motors Co")}
    sec.filings = {715153: [], 1467858: []}
    for s, cur, tz in (("HMC", "USD", "America/New_York"), ("GM", "USD", "America/New_York"),
                       ("7201.T", "JPY", "Asia/Tokyo")):
        yahoo.add(s, cur, tz, bars(s, date(2021, 9, 1)))
    yield ctx, news, sec, yahoo, web
    manager.__exit__(None, None, None)


def run(ctx, **kw):
    return backfill_history(ctx, start=START, **kw)


def test_the_companies_are_the_main_list_then_its_map_never_tesla_or_a_fund(env):
    ctx, *_ = env
    companies = history_companies(ctx)
    assert [c.symbol for c in companies] == ["HMC", "GM", "7201.T"]
    assert companies[0].main and companies[0].of == ()
    assert not companies[1].main and companies[1].of == ("HMC",)


def test_news_windows_are_calendar_quarters_from_the_start_up_to_the_last_complete_month():
    assert news_windows(START, date(2023, 1, 15)) == [Q3, Q4]
    assert news_windows(START, date(2023, 3, 2)) == [Q3, Q4, (date(2023, 1, 1), date(2023, 3, 1))]
    assert news_windows(START, date(2022, 9, 20)) == []


def test_the_news_query_names_the_company_and_the_window():
    assert news_query("Honda Motor Co.", *Q4) == '"Honda Motor" after:2022-10-01 before:2023-01-01'


@pytest.mark.parametrize("name, searched", [
    ("Meta Platforms Inc Class A", "Meta Platforms"),
    ("Space Exploration Technologies Corp. Class A Common Stock", "Space Exploration Technologies"),
    ("Mitsubishi UFJ Financial Group, Inc. Sponsored ADR", "Mitsubishi UFJ Financial"),
    ("The Procter & Gamble Company", "Procter & Gamble"),
    ("Siemens Aktiengesellschaft", "Siemens"),
    ("LVMH Moet Hennessy - Louis Vuitton, Societe Europeenne", "LVMH Moet Hennessy"),
    ("BT Group", "BT Group"),
    ("SAP SE", "SAP"),
    ("BYD Co., Ltd.", "BYD"),
    ("Eni S.p.A.", "Eni"),
])
def test_the_searched_name_is_the_one_headlines_use(name, searched):
    assert search_name(name) == searched


async def test_news_keeps_only_dated_articles_inside_the_window_asked_for_under_each_company(env):
    ctx, news, *_ = env
    q = news_query("Honda Motor Co.", *Q4)
    news.results[q] = [("https://n.test/in", "Honda sells more", "Wire", when(2022, 11, 2)),
                       ("https://n.test/undated", "Honda undated", "Wire", None),
                       ("https://n.test/late", "Honda later", "Wire", when(2023, 1, 1)),
                       ("https://n.test/early", "Honda earlier", "Wire", when(2022, 9, 30))]
    r = await run(ctx, stages=("news",))
    assert (r.kept, r.undated, r.outside) == (1, 1, 2)
    titles = [tuple(ctx.conn.execute("SELECT title, published_at FROM articles WHERE id = ?", (i,)).fetchone())
              for i in company_history(ctx, "HMC")]
    assert titles == [("Honda sells more", "2022-11-02T09:00:00+00:00")]
    w = HistoryRepo(ctx.conn).done("google_news")[("hmc", *Q4)]
    assert (w.found, w.kept, w.undated, w.outside) == (4, 1, 1, 2)


async def test_a_second_run_asks_for_nothing_already_done_and_stores_no_duplicate(env):
    ctx, news, *_ = env
    shared = ("https://n.test/both", "Honda and GM team up", "Wire", when(2022, 12, 1))
    news.results[news_query("Honda Motor Co.", *Q4)] = [shared]
    news.results[news_query("General Motors", *Q4)] = [shared]
    first = await run(ctx, stages=("news",))
    asked = len(news.queries)
    assert asked == 6 and first.requests == 6, "three companies, two windows each"
    again = await run(ctx, stages=("news",))
    assert len(news.queries) == asked and again.requests == 0
    [count] = ctx.conn.execute("SELECT COUNT(*) FROM articles WHERE url = 'https://n.test/both'").fetchone()
    assert count == 1 and company_history(ctx, "HMC") == company_history(ctx, "GM")


async def test_a_full_quarter_is_asked_again_month_by_month(env):
    ctx, news, *_ = env
    news.results[news_query("Honda Motor Co.", *Q4)] = [
        (f"https://n.test/{i}", f"Honda {i}", "Wire", when(2022, 12, 1)) for i in range(GOOGLE_CAP)]
    news.results[news_query("Honda Motor Co.", date(2022, 10, 1), date(2022, 11, 1))] = [
        ("https://n.test/oct", "Honda in October", "Wire", when(2022, 10, 5))]
    await run(ctx, stages=("news",), only=["HMC"])
    assert news.queries == [news_query("Honda Motor Co.", *w) for w in (
        Q3, Q4, (date(2022, 10, 1), date(2022, 11, 1)), (date(2022, 11, 1), date(2022, 12, 1)),
        (date(2022, 12, 1), date(2023, 1, 1)))]
    assert len(company_history(ctx, "HMC")) == GOOGLE_CAP + 1
    await run(ctx, stages=("news",), only=["HMC"])
    assert len(news.queries) == 5, "the months are resumed too"


async def test_a_lockout_backs_off_then_stops_without_hammering_and_records_nothing(env):
    ctx, news, *_ = env
    news.status = 429
    r = await run(ctx, stages=("news",))
    assert r.stopped and "google_news" in r.stopped
    assert len(news.queries) <= 12, f"asked {len(news.queries)} times while locked out"
    assert HistoryRepo(ctx.conn).done("google_news") == {}
    news.status = 200
    ctx.clock.advance(3600)
    again = await run(ctx, stages=("news",))
    assert again.stopped is None and len(HistoryRepo(ctx.conn).done("google_news")) == 6


async def test_filings_since_the_start_are_kept_for_us_names_reading_older_pages_when_needed(env):
    ctx, _, sec, *_ = env
    sec.filings[715153] = [("8-K", "2022-11-03", "0000715153-22-000009", "e.htm", "8-K"),
                           ("4", "2022-11-04", "0000715153-22-000010", "f.xml", "Form 4")]
    sec.older[715153] = [("2022-01-01", "2022-10-31", [("10-Q", "2022-10-28", "0000715153-22-000008", "q.htm", "10-Q"),
                                                          ("10-Q", "2022-07-29", "0000715153-22-000007", "p.htm", "10-Q")]),
                         ("2019-01-01", "2021-12-31", [("10-K", "2021-02-01", "0000715153-21-000001", "k.htm", "10-K")])]
    r = await run(ctx, stages=("filings",))
    rows = ctx.conn.execute("SELECT a.title, a.source, a.query FROM history_articles h JOIN articles a "
                            "ON a.id = h.article_id WHERE h.symbol = 'HMC' ORDER BY a.published_at").fetchall()
    assert [tuple(r) for r in rows] == [("10-Q", "sec_edgar", "HMC"), ("8-K", "sec_edgar", "HMC")]
    asked = [str(q.url) for q in sec.requests]
    assert not any("submissions-002" in u for u in asked), "a page wholly before the start is not read"
    assert not any("7201" in u for u in asked), "no SEC for a non-US listing"
    before = len(sec.requests)
    await run(ctx, stages=("filings",))
    assert len(sec.requests) == before and r.kept == 2


async def test_price_bars_from_2021_09_are_fetched_once_for_each_company_lacking_them(env):
    ctx, *_, yahoo, web = env
    BarRepo(ctx.conn).upsert([Bar("HMC", date(2021, 9, 1), 1, 1, 1, 9.0, 9.0, 1, "USD", "yahoo")], "2023-01-01T00:00:00Z")
    r = await run(ctx, stages=("prices",))
    assert BarRepo(ctx.conn).span(["GM"])[0] == date(2021, 9, 1)
    assert BarRepo(ctx.conn).span(["7201.T"])[0] == date(2021, 9, 1)
    charts = [str(q.url) for q in web.requests_to("query1.finance.yahoo.com") + web.requests_to("query2.finance.yahoo.com")
              if "/chart/" in str(q.url)]
    assert not any("/HMC" in u for u in charts), "HMC already has bars back to 2021-09"
    assert r.prices_added == 6
    before = len(charts)
    await run(ctx, stages=("prices",))
    after = [q for q in web.requests_to("query1.finance.yahoo.com") + web.requests_to("query2.finance.yahoo.com")
             if "/chart/" in str(q.url)]
    assert len(after) == before


async def test_stop_ends_the_run_between_requests_and_busy_live_work_is_waited_out(env):
    ctx, news, *_ = env
    calls = []

    def busy():
        calls.append(1)
        return "Research HMC" if len(calls) <= 2 else None

    r = await run(ctx, stages=("news",), busy=busy, stop=lambda: len(news.queries) >= 2)
    assert r.stopped == "cancelled" and len(news.queries) == 2
    assert 5.0 in ctx.clock.sleeps, "waited in steps while live work ran"


async def test_coverage_counts_articles_and_outlets_per_company_and_quarter_and_names_thin_ones(env):
    ctx, news, sec, *_ = env
    news.results[news_query("Honda Motor Co.", *Q4)] = [
        ("https://n.test/1", "Honda a", "Wire", when(2022, 11, 2)),
        ("https://n.test/2", "Honda b", "Paper", when(2022, 12, 2))]
    sec.filings[715153] = [("8-K", "2022-11-03", "0000715153-22-000009", "e.htm", "8-K")]
    await run(ctx)
    cov = coverage(ctx)
    assert cov.quarters == ("2022-Q3", "2022-Q4")
    hmc = next(r for r in cov.rows if r.symbol == "HMC")
    assert (hmc.cells["2022-Q4"].news, hmc.cells["2022-Q4"].filings, hmc.cells["2022-Q4"].outlets) == (2, 1, 3)
    assert hmc.thin == ("2022-Q3", "2022-Q4")
    assert cov.windows_left == 0 and cov.windows_done == 6


async def test_the_backfill_is_one_activity_job_with_progress_that_cancel_stops(env):
    ctx, news, *_ = env
    runner = JobRunner(ctx)
    job = submit_history(ctx, runner)
    assert submit_history(ctx, runner) is job, "pressed twice joins the first"
    done = await runner.wait(job.id)
    assert done.state == "done" and done.kind == "history" and done.result.requests > 0
    assert job.progress_fn() is not None


async def test_a_cancelled_backfill_is_retried_on_the_same_list(env):
    ctx, *_ = env
    runner = JobRunner(ctx)
    job = submit_history(ctx, runner)
    runner.cancel(job.id)
    again = retry(ctx, runner, job.id)
    assert again.id != job.id and again.subject == job.subject == "history|main"
    await runner.wait(again.id)
