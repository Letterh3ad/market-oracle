"""SEC XBRL fundamentals of the testbed's members: fetched into the research database only,
resumable, and used point in time (from the session after acceptance, first-filed values, splits
in today's shares) by the three fundamental signals."""

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.analysts.signal_test import Panel
from oracle.config import Config, ResearchConfig
from oracle.market.bars import Bar
from oracle.services.context import open_context
from oracle.services.signal_test_service import SIGNALS, load_panel, run_signal_test
from oracle.services.testbed_service import Testbed, research_context
from oracle.services.xbrl_service import (
    NO_CIK, NO_FACTS, earnings_yield, fetch_fundamentals, fundamentals_coverage, gross_profitability, sue, timelines,
)
from oracle.store.bars import BarRepo
from oracle.store.testbed import Snapshot, TestbedRepo
from oracle.store.xbrl import XbrlRepo
from tests.fakes.clock import FakeClock
from tests.fakes.sec import FakeSec
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2019, 6, 3, 12, 0, tzinfo=timezone.utc)
NY = "America/New_York"
AT = "2019-01-01T00:00:00+00:00"
D = date.fromisoformat
QE = ("03-31", "06-30", "09-30", "12-31")
QS = ("01-01", "04-01", "07-01", "10-01")


def weekdays(a: date, b: date) -> list[date]:
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


def eps_rows(values, first_year=2014, accn="q"):
    """Quarterly diluted EPS, the fourth quarter as the year (10-K), each filed 40 days after its end;
    returns (facts, (accession, form, filed) per filing)."""
    facts, filings, y, k = [], [], first_year, 0
    year_sum = 0.0
    for n, v in enumerate(values):
        end = D(f"{y}-{QE[k]}")
        filed = (end + timedelta(days=40)).isoformat()
        a = f"{accn}-{n:03d}"
        if k < 3:
            facts.append(("EarningsPerShareDiluted", f"{y}-{QS[k]}", end.isoformat(), v, a, "10-Q", filed))
            filings.append((a, "10-Q", filed))
        else:
            facts.append(("EarningsPerShareDiluted", f"{y}-01-01", end.isoformat(), year_sum + v, a, "10-K", filed))
            filings.append((a, "10-K", filed))
        year_sum = 0.0 if k == 3 else year_sum + v
        k = (k + 1) % 4
        y += k == 0
    return facts, filings


def body(rows):
    out: dict = {}
    for concept, start, end, val, accn, form, filed in rows:
        unit = "USD/shares" if concept.startswith("EarningsPerShare") else "USD"
        r = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed, "fy": 2019, "fp": "Q1"}
        if start:
            r["start"] = start
        out.setdefault(concept, {"units": {unit: []}})["units"][unit].append(r)
    return {"cik": 1, "entityName": "x", "facts": {"us-gaap": out}}


@pytest.fixture
def env(tmp_path):
    sec, yahoo = FakeSec(), FakeYahoo()
    web = FakeWeb(sec, yahoo)
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db",
                    research=ResearchConfig(contact="Test test@example.com"))
    with open_context(config, None, FakeClock(NOW), web.transport) as ctx:
        yield ctx, sec, yahoo, web


def seed(ctx, members=("AAA", "BBB", "BRK-B", "NOSEC", "GONE"), priced=("AAA", "BBB", "BRK-B", "NOSEC")):
    with research_context(ctx) as r:
        repo = TestbedRepo(r.conn)
        repo.save_snapshot(Snapshot("fja05680/sp500", "https://x", "abc", AT, D("2010-01-04"), D("2010-01-04"), 1),
                           [(D("2010-01-04"), tuple(members))])
        for t in members:
            repo.note_bars(t, AT, None if t in priced else "not on Yahoo")


async def test_fetch_stores_facts_acceptance_and_splits_in_the_research_database_only(env):
    ctx, sec, yahoo, web = env
    seed(ctx)
    facts, filings = eps_rows([1.0] * 4)
    sec.companies = {"AAA": (1, "Aaa"), "BBB": (2, "Bbb"), "BRK-B": (3, "Berkshire")}
    sec.facts = {1: body(facts + [("Assets", None, "2014-03-31", 100.0, "q-000", "10-Q", "2014-05-10"),
                                  ("NetIncomeLoss", "2014-01-01", "2014-03-31", 5.0, "q-000", "10-Q", "2014-05-10")]),
                 3: body(facts)}
    sec.filings = {1: [("10-Q", "2014-05-12", "q-000", "a.htm", "", "", "2014-05-10T22:01:00.000Z"),
                       ("4", "2014-05-12", "z", "b.htm", "", "", "2014-05-12T10:00:00.000Z")],
                   3: []}
    yahoo.add("AAA", "USD", NY, [bar("2014-01-02", 10.0)])
    yahoo.splits["AAA"] = [(D("2016-05-02"), 2, 1)]
    before = list(ctx.conn.iterdump())
    run = await fetch_fundamentals(ctx)
    assert list(ctx.conn.iterdump()) == before, "the live database is untouched"
    assert (run.members, run.mapped, run.no_cik, run.with_facts, run.no_facts) == (4, 3, 1, 2, 1), run.summary
    with research_context(ctx) as r:
        repo = XbrlRepo(r.conn)
        m = repo.members()
        assert (m["NOSEC"].facts_note, m["BBB"].facts_note, m["AAA"].facts_note) == (NO_CIK, NO_FACTS, None)
        assert "GONE" not in m, "an unpriced member is never asked for"
        assert len(repo.facts(1)) == 5 and {f.concept for f in repo.facts(1)} == {"EarningsPerShareDiluted", "Assets"}
        assert repo.accepted(1) == {"q-000": "2014-05-10T18:01"}, "periodic reports only, in New York time"
        assert repo.splits("AAA") == [(D("2016-05-02"), 2.0)]
    agent = {r.headers["user-agent"] for r in web.requests_to("data.sec.gov")}
    assert agent == {"market-oracle/0.1 Test test@example.com"}
    asked = len(web.requests)
    again = await fetch_fundamentals(ctx)
    assert again.requests == 0 and len(web.requests) == asked, "resumes: nothing tried is asked again"


async def test_a_fetch_without_a_contact_stops_and_says_so(env):
    ctx, sec, *_ = env
    seed(ctx)
    run = await fetch_fundamentals(replace(ctx, config=replace(ctx.config, research=ResearchConfig())))
    assert run.stopped and "contact" in run.stopped and not sec.requests


async def test_a_cancelled_fetch_keeps_what_it_has(env):
    ctx, sec, *_ = env
    seed(ctx)
    sec.companies = {"AAA": (1, "Aaa")}
    run = await fetch_fundamentals(ctx, stop=lambda: True)
    assert run.stopped == "cancelled"


def stored(ctx, cik, rows, accepted=None, ticker="AAA", splits=()):
    from oracle.research.xbrl import parse_companyfacts
    with research_context(ctx) as r:
        repo = XbrlRepo(r.conn)
        repo.replace_facts(cik, parse_companyfacts(body(rows)), accepted or {})
        repo.note_facts(ticker, cik, AT, None)
        repo.replace_splits(ticker, splits, AT)


def panel(days, tickers, closes=None):
    p = Panel(days, [frozenset(tickers)] * len(days), {})
    for t in tickers:
        c = (closes or {}).get(t, 10.0)
        p.add(t, [Bar(t, d, c, c, c, c, c, 1, "USD", "yahoo") for d in days])
    return p


def test_a_filing_counts_from_the_session_after_its_acceptance_and_a_restatement_never_does(env):
    ctx, *_ = env
    seed(ctx)
    values = [1, 1, 1, 1, 2, 1, 2, 1, 2, 3, 2, 3, 4]
    rows, filings = eps_rows(values)
    last_accn, _, filed = filings[-1]
    # A later 10-Q restates the last quarter: never what was known then.
    rows.append(("EarningsPerShareDiluted", "2017-01-01", "2017-03-31", -50.0, "restated", "10-Q/A", "2017-06-20"))
    accepted = {last_accn: "2017-05-09T18:30"}
    stored(ctx, 1, rows, accepted)
    days = weekdays(D("2017-05-01"), D("2017-07-31"))
    p = panel(days, ["AAA"])
    with research_context(ctx) as r:
        p.extra["xbrl"] = timelines(r, ["AAA"])
    on = {d: sue(p, p.at[d], ["AAA"]).get("AAA") for d in (D("2017-05-09"), D("2017-05-10"), D("2017-07-03"))}
    assert on[D("2017-05-09")] != on[D("2017-05-10")], "accepted 05-09 after the close: used from 05-10"
    assert on[D("2017-05-10")] == on[D("2017-07-03")] and on[D("2017-05-10")] > 0, "held, restatement ignored"
    assert filed == "2017-05-10"


def test_earnings_yield_puts_eps_in_todays_shares_before_dividing_by_the_split_adjusted_close(env):
    ctx, *_ = env
    seed(ctx)
    rows, _ = eps_rows([1.0, 1.0, 1.0, 1.0], first_year=2016)
    stored(ctx, 1, rows, splits=[(D("2017-06-01"), 2.0)])
    days = weekdays(D("2017-03-01"), D("2017-03-31"))
    p = panel(days, ["AAA"], {"AAA": 20.0})
    with research_context(ctx) as r:
        p.extra["xbrl"] = timelines(r, ["AAA"])
    # TTM EPS 4.0 known 2017-02-09; a 2-for-1 split after it: 2.0 in today's shares over 20.0.
    assert earnings_yield(p, p.at[D("2017-03-15")], ["AAA"]) == {"AAA": pytest.approx(0.1)}


def test_gross_profitability_is_trailing_gross_profit_over_assets_at_the_quarters_end(env):
    ctx, *_ = env
    seed(ctx)
    rows = [("GrossProfit", f"2016-{QS[k]}", f"2016-{QE[k]}", 10.0, f"g{k}", "10-Q", f"2016-{5 + 3 * k:02d}-10")
            for k in range(3)]
    rows += [("GrossProfit", "2016-01-01", "2016-12-31", 45.0, "g3", "10-K", "2017-02-20"),
             ("Assets", None, "2016-12-31", 300.0, "g3", "10-K", "2017-02-20")]
    stored(ctx, 1, rows)
    days = weekdays(D("2017-02-15"), D("2017-03-31"))
    p = panel(days, ["AAA"])
    with research_context(ctx) as r:
        p.extra["xbrl"] = timelines(r, ["AAA"])
    assert gross_profitability(p, p.at[D("2017-02-20")], ["AAA"]) == {}
    assert gross_profitability(p, p.at[D("2017-02-21")], ["AAA"]) == {"AAA": pytest.approx(0.15)}


def test_a_score_older_than_the_hold_is_not_used(env):
    ctx, *_ = env
    seed(ctx)
    rows, _ = eps_rows([1.0] * 4, first_year=2016)
    stored(ctx, 1, rows)
    days = weekdays(D("2017-02-01"), D("2017-12-29"))
    p = panel(days, ["AAA"])
    with research_context(ctx) as r:
        p.extra["xbrl"] = timelines(r, ["AAA"])
    assert earnings_yield(p, p.at[D("2017-03-01")], ["AAA"])
    assert earnings_yield(p, p.at[D("2017-12-01")], ["AAA"]) == {}, "known 2017-02-09, over 200 days old"


def test_the_fundamental_signals_keep_the_priced_listing_of_a_duplicate_pair(env):
    ctx, *_ = env
    with research_context(ctx) as r:
        TestbedRepo(r.conn).save_snapshot(Snapshot("s", "u", None, AT, D("2017-01-02"), D("2017-01-02"), 1),
                                          [(D("2017-01-02"), ("KORS", "CPRI", "AAA"))])
        BarRepo(r.conn).upsert([bar_of("CPRI", d) for d in weekdays(D("2017-01-02"), D("2017-01-31"))]
                               + [bar_of("SPY", d) for d in weekdays(D("2017-01-02"), D("2017-01-31"))], AT)
        tb = Testbed(r)
        old = load_panel(tb, D("2017-01-31"), D("2017-01-02"))
        new = load_panel(tb, D("2017-01-31"), D("2017-01-02"), keep_priced_duplicate=True)
    assert old.members[0] == {"KORS", "AAA"}, "as ticker 02 pre-registered: the later listing dropped"
    assert new.members[0] == {"CPRI", "AAA"}
    assert all(SIGNALS[n].keep_priced_duplicate for n in ("sue", "gross_profitability", "earnings_yield"))


def bar_of(t, d):
    return Bar(t, d, 10.0, 10.0, 10.0, 10.0, 10.0, 1, "USD", "yahoo")


async def test_a_train_only_run_reads_no_fundamentals_known_after_2019(env, monkeypatch):
    ctx, *_ = env
    seed(ctx)
    seen = []
    import oracle.services.xbrl_service as svc
    real = svc.timelines
    monkeypatch.setattr(svc, "timelines", lambda r, tickers, through=None: seen.append(through) or real(r, tickers,
                                                                                                         through))
    with research_context(ctx) as r:
        BarRepo(r.conn).upsert([bar_of("SPY", d) for d in weekdays(D("2019-12-02"), D("2020-01-31"))], AT)
    test = await run_signal_test(ctx, "sue", train_only=True)
    assert seen == [D("2019-12-31")] and test.verdict == "TRAIN ONLY"


def test_coverage_counts_members_with_a_cik_facts_and_scored_filings_per_year(env):
    ctx, *_ = env
    seed(ctx)
    rows, _ = eps_rows([1, 1, 1, 1, 2, 1, 2, 1, 2, 3, 2, 3, 4], first_year=2014)
    stored(ctx, 1, rows)
    with research_context(ctx) as r:
        XbrlRepo(r.conn).note_facts("NOSEC", None, AT, NO_CIK)
    c = fundamentals_coverage(ctx)
    assert (c.priced, c.with_cik, c.with_facts, c.without_cik) == (4, 1, 1, ("NOSEC",))
    y = {row.year: row for row in c.years}
    assert y[2016].members == 4 and y[2016].with_facts == 1
    assert y[2016].scored["sue"] == 1 and y[2016].events["sue"] == 3, "2016 Q2 to Q4 have 4 changes before them"
    assert y[2015].events["earnings_yield"] == 4 and y[2015].events["sue"] == 0


async def test_the_fetch_job_retries_as_it_was_and_a_signal_test_waits_for_it(env):
    from oracle.services.activity_service import JobRunner, _signal_test_waits, retry, submit_xbrl
    ctx, sec, *_ = env
    seed(ctx)
    sec.companies = {"AAA": (1, "Aaa")}
    runner = JobRunner(ctx)
    job = submit_xbrl(ctx, runner, refresh=True)
    assert submit_xbrl(ctx, runner, refresh=True) is job, "pressed twice joins the first"
    runner.cancel(job.id)
    again = await runner.wait(retry(ctx, runner, job.id).id)
    assert again.label == "Refetch SEC fundamentals" and again.state == "done", again.error
    hold = asyncio.Event()

    async def fetching(c):
        await hold.wait()

    other = runner.submit("xbrl", "Fetch SEC fundamentals", fetching, subject="xbrl")
    await asyncio.sleep(0.05)
    assert _signal_test_waits(ctx, runner) == "Fetch SEC fundamentals"
    hold.set()
    await runner.wait(other.id)
    assert _signal_test_waits(ctx, runner) is None
