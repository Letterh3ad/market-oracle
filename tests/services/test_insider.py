"""Form 4 insider trades and typed filings of the testbed's members: fetched into the research
database only, resumable, used point in time by `insider_buys` and the typed-event study."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.analysts.event_study import clustered_t, passes, summarise
from oracle.analysts.signal_test import Panel
from oracle.config import Config, ResearchConfig
from oracle.market.bars import Bar
from oracle.research import insider
from oracle.research.insider import Filing, Trade
from oracle.services.context import open_context
from oracle.services.event_study_service import event_studies, run_and_keep_events, run_event_study
from oracle.services.insider_service import fetch_insiders, insider_buys, insider_coverage, issuer_data
from oracle.services.signal_test_service import SIGNALS, signal_tests
from oracle.services.testbed_service import research_context
from oracle.store.bars import BarRepo
from oracle.store.insider import InsiderRepo
from oracle.store.testbed import Snapshot, TestbedRepo
from oracle.store.xbrl import XbrlRepo
from tests.fakes.clock import FakeClock
from tests.fakes.sec import FakeSec
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo
from tests.research.test_insider import dataset

NOW = datetime(2021, 6, 3, 12, 0, tzinfo=timezone.utc)
AT = "2019-01-01T00:00:00+00:00"
D = date.fromisoformat


def weekdays(a: date, b: date) -> list[date]:
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


@pytest.fixture
def env(tmp_path):
    sec, yahoo = FakeSec(), FakeYahoo()
    web = FakeWeb(sec, yahoo)
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db",
                    research=ResearchConfig(contact="Test test@example.com"))
    with open_context(config, None, FakeClock(NOW), web.transport) as ctx:
        yield ctx, sec, web


def seed(ctx, members=("AAA", "BBB", "NOSEC", "GONE"), priced=("AAA", "BBB", "NOSEC"), ciks=None):
    with research_context(ctx) as r:
        repo = TestbedRepo(r.conn)
        repo.save_snapshot(Snapshot("fja05680/sp500", "https://x", "abc", AT, D("2010-01-04"), D("2010-01-04"), 1),
                           [(D("2010-01-04"), tuple(members))])
        for t in members:
            repo.note_bars(t, AT, None if t in priced else "not on Yahoo")
        XbrlRepo(r.conn).replace_tickers(ciks or {"AAA": (1, "Aaa"), "BBB": (2, "Bbb"), "GONE": (3, "Gone")}, NOW.isoformat())


async def test_fetch_stores_filings_and_trades_in_the_research_database_only_and_resumes(env):
    ctx, sec, web = env
    seed(ctx)
    sec.filings = {1: [("4", "2010-03-31", "a1", "f.xml", "", "", "2010-03-31T01:00:00.000Z"),
                       ("8-K", "2010-04-20", "k1", "k.htm", "", "2.02,9.01", "2010-04-20T20:05:00.000Z"),
                       ("10-Q", "2010-05-01", "q1", "q.htm", "", "", "2010-05-01T20:05:00.000Z")],
                   2: []}
    sec.datasets = {"2010q1": dataset(
        [("a1", "31-MAR-2010", "30-MAR-2010", "4", "0000000001", "AAA"),
         ("a9", "31-MAR-2010", "30-MAR-2010", "4", "0000000003", "GONE")],
        [("a1", "0000000500", "Doe", "Director", ""), ("a9", "0000000501", "X", "Officer", "")],
        [("a1", "1", "29-MAR-2010", "P", "100.0", "8.5", "A"), ("a9", "1", "29-MAR-2010", "P", "1.0", "1.0", "A")])}
    before = list(ctx.conn.iterdump())
    run = await fetch_insiders(ctx)
    assert list(ctx.conn.iterdump()) == before, "the live database is untouched"
    assert (run.members, run.issuers, run.no_cik, run.filings_fetched, run.quarters_fetched, run.trades_added) == \
        (3, 2, 1, 2, 1, 1), run.summary
    with research_context(ctx) as r:
        repo = InsiderRepo(r.conn)
        assert [f.accession for f in repo.filings(1)] == ["a1", "k1"], "Form 4s and 8-Ks only"
        assert repo.accepted(1) == {"a1": "2010-03-30T21:00", "k1": "2010-04-20T16:05"}, "New York time"
        assert [(t.accession, t.code, t.value) for t in repo.trades(1)] == [("a1", "P", 850.0)]
        assert repo.trades(3) == [], "an unpriced member's issuer is never kept"
    assert {r.headers["user-agent"] for r in web.requests_to("www.sec.gov")} == {"market-oracle/0.1 Test test@example.com"}
    again = await fetch_insiders(ctx)
    assert again.requests == 1 and again.quarters_fetched == 0, "resumes: only the data sets page is read again"


async def test_a_fetch_without_a_contact_stops_and_says_so(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    sec = FakeSec()
    with open_context(config, None, FakeClock(NOW), FakeWeb(sec).transport) as ctx:
        seed(ctx)
        run = await fetch_insiders(ctx)
    assert run.stopped and "contact" in run.stopped


def store(ctx, cik, trades=(), filings=(), quarters=("2020q4",), issuers=(1, 2)):
    with research_context(ctx) as r:
        repo = InsiderRepo(r.conn)
        for q in quarters:
            repo.replace_quarter(q, "u", [t for t in trades if insider.quarter_of(t.filed) == q], issuers, AT)
        repo.replace_filings(cik, list(filings), AT)


def form4(a, cik, filed, accepted=None):
    return Filing(a, cik, "4", "", D(filed), accepted)


def buy(a, owner, filed, cik=1, role="Officer", code="P"):
    return Trade(a, cik, owner, "x", role, code, D(filed), 10.0, 5.0, D(filed))


def panel(days, tickers, opens=None):
    p = Panel(days, [frozenset(tickers)] * len(days), {t: "Tech" for t in tickers})
    for t in tickers:
        o = (opens or {}).get(t, lambda i: 10.0)
        p.add(t, [Bar(t, d, o(i), o(i), o(i), o(i), o(i), 1, "USD", "yahoo") for i, d in enumerate(days)])
    return p


def test_insider_buys_counts_from_the_session_after_acceptance_scores_zero_and_stops_with_the_data(env):
    ctx, *_ = env
    seed(ctx)
    store(ctx, 1, [buy("a", 7, "2020-11-03"), buy("b", 8, "2020-11-05"), buy("c", 9, "2020-11-05", code="S")],
          [form4("old", 1, "2019-05-01"), form4("a", 1, "2020-11-03", "2020-11-02T19:00")],
          quarters=("2019q2", "2019q3", "2019q4", "2020q1", "2020q2", "2020q3", "2020q4"))
    store(ctx, 2, filings=[form4("b0", 2, "2019-01-02")], quarters=())
    days = weekdays(D("2020-10-01"), D("2021-01-29"))
    p = panel(days, ["AAA", "BBB", "NOSEC"])
    with research_context(ctx) as r:
        p.extra["insider"] = issuer_data(r, ["AAA", "BBB", "NOSEC"])
    on = lambda d: insider_buys(p, p.at[D(d)], ["AAA", "BBB", "NOSEC"])  # noqa: E731
    assert on("2020-11-02") == {"AAA": 0.0, "BBB": 0.0}, "no CIK, no score; a CIK without buys scores 0"
    assert on("2020-11-03") == {"AAA": 1.0, "BBB": 0.0}, "accepted 11-02 in New York: used from 11-03"
    assert on("2020-11-06")["AAA"] == 2.0, "a sale never counts"
    assert on("2020-12-31")["AAA"] == 2.0
    assert on("2021-01-04") == {}, "after the last stored quarter there is no insider data, not zeros"
    assert SIGNALS["insider_buys"].horizons == (20, 60) and SIGNALS["insider_buys"].keep_priced_duplicate


def test_clustered_t_and_the_pass_rule():
    cars = [(D("2020-01-02"), 0.02), (D("2020-01-02"), 0.04), (D("2020-01-03"), -0.01), (D("2020-01-06"), 0.03)]
    m, t = clustered_t(cars)
    assert m == pytest.approx(0.02)
    # Clusters' summed deviations 0.02, -0.03, 0.01; var = 3/2 * 0.0014 / 16.
    assert t == pytest.approx(0.02 / (1.5 * 0.0014 / 16) ** 0.5)
    row = summarise(cars, 20)
    assert (row.n, row.days, row.positive) == (4, 3, 3)
    pos, neg = summarise([(D("2020-01-02"), 0.01)] * 2 + [(D("2020-01-03"), 0.02)], 20), \
        summarise([(D("2020-01-02"), -0.01), (D("2020-01-03"), -0.02)], 20)
    big = summarise([(D("2021-01-01") + timedelta(n), 0.01 + 0.001 * (n % 3)) for n in range(30)], 20)
    assert passes(pos, big) and not passes(neg, big), "confirm t above 2 in the train sign only"


async def test_event_study_measures_the_sector_excess_from_the_next_sessions_open_train_and_confirm(env):
    ctx, *_ = env
    tickers = [f"T{n}" for n in range(6)]
    seed(ctx, members=tickers, priced=tickers, ciks={"T0": (1, "x")})
    days = weekdays(D("2019-06-03"), D("2021-05-28"))
    # T0's 8-K 2.02 accepted after the close on day k: it jumps 10% at the next open, then holds.
    events = [D("2019-08-14"), D("2020-08-13")]
    store(ctx, 1, filings=[Filing(f"k{n}", 1, "8-K", "2.02", d, f"{d}T17:00") for n, d in enumerate(events)])
    with research_context(ctx) as r:
        TestbedRepo(r.conn).replace_sectors({t: "Tech" for t in tickers}) if hasattr(TestbedRepo, "replace_sectors") \
            else None
        jump = [days.index(d) + 2 for d in events]
        rows = []
        for t in tickers:
            for i, d in enumerate(days):
                o = 10.0 * (1.1 ** sum(i >= j for j in jump)) if t == "T0" else 10.0
                rows.append(Bar(t, d, o, o, o, o, o, 1, "USD", "yahoo"))
        rows += [Bar("SPY", d, 1.0, 1.0, 1.0, 1.0, 1.0, 1, "USD", "yahoo") for d in days]
        BarRepo(r.conn).upsert(rows, AT)
    study = await run_and_keep_events(ctx)
    t202 = next(t for t in study.types if t.name == "2.02")
    # Entry at the open of the session after the event day; the jump is at the open after that.
    tr = {r.horizon: r for r in t202.train}
    co = {r.horizon: r for r in t202.confirm}
    assert tr[1].n == 1 and tr[1].mean == pytest.approx(0.1, abs=1e-6)
    assert co[20].n == 1 and co[20].mean == pytest.approx(0.1, abs=1e-6)
    assert t202.verdict == "INCONCLUSIVE", "one event: no clustered t"
    assert event_studies(ctx)["full"].types[0].train == study.types[0].train
    assert "event_study" not in signal_tests(ctx), "kept beside the signals, never read as one"
    train = await run_event_study(ctx, train_only=True)
    assert train.train_only and all(t.verdict == "TRAIN ONLY" and not t.confirm for t in train.types)
    assert train.last_day <= D("2019-12-31")


def test_coverage_counts_trades_and_items_per_year(env):
    ctx, *_ = env
    seed(ctx)
    store(ctx, 1, [buy("a", 7, "2020-11-03"), buy("c", 9, "2020-11-05", code="S")],
          [Filing("k", 1, "8-K", "2.02,1.01", D("2020-04-01"), None), Filing("a", 1, "4", "", D("2020-11-03"), None)])
    c = insider_coverage(ctx)
    assert (c.priced, c.issuers, c.no_cik, c.filings_fetched) == (3, 2, 1, 1)
    assert c.quarters == {"2020q4": 2}
    assert c.years[2020]["P"] == 1 and c.years[2020]["S"] == 1 and c.years[2020]["8-K 2.02"] == 1
    assert c.years[2020]["4"] == 1


async def test_the_fetch_job_retries_as_it_was_and_a_signal_test_waits_for_it(env):
    from oracle.services.activity_service import JobRunner, _signal_test_waits, retry, submit_insiders
    ctx, *_ = env
    seed(ctx)
    runner = JobRunner(ctx)
    job = submit_insiders(ctx, runner, refresh=True)
    assert submit_insiders(ctx, runner, refresh=True) is job, "pressed twice joins the first"
    runner.cancel(job.id)
    again = await runner.wait(retry(ctx, runner, job.id).id)
    assert again.label == "Refetch Form 4s and 8-Ks" and again.state == "done", again.error
    hold = asyncio.Event()

    async def fetching(c):
        await hold.wait()

    other = runner.submit("insiders", "Fetch Form 4s and 8-Ks", fetching, subject="insiders")
    await asyncio.sleep(0.05)
    assert _signal_test_waits(ctx, runner) == "Fetch Form 4s and 8-Ks"
    hold.set()
    await runner.wait(other.id)


def test_a_member_is_unscored_until_its_cik_has_90_days_of_form4s_and_data_sets_fetched_with_it(env):
    ctx, *_ = env
    seed(ctx)
    # AAA's CIK files its first Form 4 on 2020-03-02 (a new holding company): unscored before 2020-06-01.
    # BBB's CIK never filed a Form 4 (as XOM's today): never scored.
    store(ctx, 1, filings=[form4("f", 1, "2020-03-02")], quarters=("2019q4", "2020q1", "2020q2", "2020q3"),
          issuers=(1, 2))
    # A later quarter fetched without AAA's CIK: nothing it covers is scored for AAA.
    store(ctx, 1, filings=[form4("f", 1, "2020-03-02")], quarters=("2020q4",), issuers=(2,))
    days = weekdays(D("2020-05-01"), D("2020-12-31"))
    p = panel(days, ["AAA", "BBB"])
    with research_context(ctx) as r:
        p.extra["insider"] = issuer_data(r, ["AAA", "BBB"])
    on = lambda d: insider_buys(p, p.at[D(d)], ["AAA", "BBB"])  # noqa: E731
    assert on("2020-05-29") == {}, "AAA's first Form 4 is under 90 days old; BBB has none"
    assert on("2020-06-01") == {"AAA": 0.0}
    assert on("2020-09-30") == {"AAA": 0.0}
    assert on("2020-10-01") == {"AAA": 0.0} and on("2020-10-02") == {}, "the window reaches 2020q4, fetched without AAA's CIK"


async def test_a_smoke_run_then_a_full_run_stores_every_issuers_trades(env):
    ctx, sec, _ = env
    seed(ctx)
    sec.filings = {1: [("4", "2010-03-31", "a1", "f.xml", "", "", "2010-03-31T01:00:00.000Z")],
                   2: [("4", "2010-03-31", "a2", "f.xml", "", "", "2010-03-31T01:00:00.000Z")]}
    sec.datasets = {"2010q1": dataset(
        [("a1", "31-MAR-2010", "30-MAR-2010", "4", "0000000001", "AAA"),
         ("a2", "31-MAR-2010", "30-MAR-2010", "4", "0000000002", "BBB")],
        [("a1", "0000000500", "Doe", "Director", ""), ("a2", "0000000501", "X", "Officer", "")],
        [("a1", "1", "29-MAR-2010", "P", "100.0", "8.5", "A"), ("a2", "1", "29-MAR-2010", "P", "1.0", "1.0", "A")])}
    smoke = await fetch_insiders(ctx, only=["AAA"])
    assert smoke.quarters_fetched == 1 and smoke.trades_added == 1
    full = await fetch_insiders(ctx)
    assert full.quarters_fetched == 1, "the quarter lacked BBB's CIK: fetched again"
    with research_context(ctx) as r:
        repo = InsiderRepo(r.conn)
        assert len(repo.trades(1)) == 1 and len(repo.trades(2)) == 1
        assert repo.quarter_issuers()["2010q1"] == frozenset({1, 2})
    again = await fetch_insiders(ctx, only=["AAA"], refresh=True)
    with research_context(ctx) as r:
        assert InsiderRepo(r.conn).quarter_issuers()["2010q1"] == frozenset({1, 2}), "a smoke refetch never shrinks"
        assert len(InsiderRepo(r.conn).trades(2)) == 1
    assert again.quarters_fetched == 1
    assert (await fetch_insiders(ctx)).quarters_fetched == 0


def test_a_filing_listed_under_two_member_ciks_is_kept_under_both(env):
    ctx, *_ = env
    seed(ctx)
    with research_context(ctx) as r:
        repo = InsiderRepo(r.conn)
        repo.replace_filings(1, [form4("x", 1, "2020-01-03", "2020-01-02T18:00")], AT)
        repo.replace_filings(2, [form4("x", 2, "2020-01-03", "2020-01-02T18:00")], AT)
        assert repo.accepted(1) == {"x": "2020-01-02T18:00"} and repo.accepted(2) == {"x": "2020-01-02T18:00"}


def test_coverage_lists_members_whose_cik_starts_late(env):
    ctx, *_ = env
    seed(ctx)
    store(ctx, 1, filings=[form4("f", 1, "2015-03-02")], quarters=("2010q1",), issuers=(1, 2))
    c = insider_coverage(ctx)
    assert [(x.ticker, x.member_from, x.scored_from) for x in c.late] == [
        ("AAA", D("2010-01-04"), D("2015-05-31")), ("BBB", D("2010-01-04"), None)]
