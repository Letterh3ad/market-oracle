"""Renames and reused tickers in the testbed: a rename is priced from the ticker it became only
when SEC (the filer's covers name the old ticker before and the new one after) and Yahoo (the new
series covers the old member-days and runs across the change) agree; acquisitions are kept for
review; chains resolve; SEC overrides Yahoo's first-trade day for a former member's identity."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from oracle.config import Config, ResearchConfig
from oracle.research import edgar
from oracle.services.context import open_context
from oracle.services.testbed_renames import Candidate, company_name, swap_candidates
from oracle.services.testbed_service import REUSED, TAIL, build_testbed, open_testbed, research_context
from oracle.services.testbed_service import testbed_coverage as coverage_of
from oracle.store.testbed import Span, TestbedRepo
from tests.fakes.sec import FakeSec, cover
from tests.fakes.sp500 import FakeSp500
from tests.fakes.web import FakeWeb
from tests.fakes.wikipedia import FakeWikipedia
from tests.fakes.yahoo import FakeYahoo, bar, quote

NOW = datetime(2012, 12, 31, 12, 0, tzinfo=timezone.utc)
NY = "America/New_York"
BASE = "AAA,BBB,SUN,PPP"
ROWS = [
    ("2010-01-04", f"{BASE},FB,EA,SYMC,GPS,DISCA,DISCK"),
    ("2011-03-01", f"{BASE},FB,EA,SYMC,CCC,DISCA,DISCK"),
    ("2011-06-01", f"{BASE},FB,EA,NLOK,CCC,DISCA,DISCK"),
    ("2012-01-03", f"{BASE},META,EA,NLOK,CCC,DISCA,DISCK"),
    ("2012-03-01", f"{BASE},META,FERG,NLOK,CCC,DISCA,DISCK"),
    ("2012-04-02", f"{BASE},META,FERG,NLOK,CCC,WBD"),
    ("2012-06-01", f"{BASE},META,FERG,GEN,CCC,WBD"),
    ("2012-09-04", "AAA,DDD,META,FERG,GEN,CCC,WBD"),
]
CIKS = {"META": 1326801, "FERG": 2011641, "GEN": 849399, "CCC": 3333, "GAP": 39911, "SUN": 1552275, "BBB": 5555,
        "WBD": 1437107, "AAA": 1111, "DDD": 4444, "PPP": 1585364}


def weekdays(start: str, end: str) -> list[date]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


def series(start: str = "2009-06-01", end: str = "2012-12-28") -> list[tuple]:
    return [bar(d.isoformat(), 10.0) for d in weekdays(start, end)]


def reports(sec: FakeSec, cik: int, covers: list[tuple[str, str]]) -> None:
    """10-Qs filed on each day whose cover names the symbols (slash-separated)."""
    rows = []
    for n, (day, symbols) in enumerate(covers):
        accession = f"{cik:010d}-{day[2:4]}-{n:06d}"
        rows.append(("10-Q", day, accession, f"q{n}.htm", "10-Q"))
        sec.documents[edgar.cover_url(cik, accession)] = cover(*symbols.split("/"))
    sec.filings[cik] = sorted(rows, key=lambda r: r[1], reverse=True)


@pytest.fixture
def env(tmp_path):
    yahoo, sec, gh = FakeYahoo(), FakeSec(), FakeSp500(ROWS)
    for s in ("SPY", "ACWI", "AAA", "META", "FERG", "GEN", "CCC", "GAP", "WBD", "DDD", "PPP"):
        yahoo.add(s, "USD", NY, series())
    yahoo.add("SUN", "USD", NY, series("2010-01-04"))
    yahoo.add("BBB", "USD", NY, series("2010-09-20"), first_trade=date(2010, 9, 20))
    yahoo.quotes["GPS"] = [quote("GPSI34.SA", "The Gap, Inc."), quote("GPSC.BK", "Global Power Synergy")]
    sec.companies = {t: (cik, {"GAP": "Gap Inc", "SUN": "Sunoco LP"}.get(t, f"{t} Corp")) for t, cik in CIKS.items()}
    sec.companies["EA"] = (712515, "Electronic Arts Inc")
    reports(sec, CIKS["META"], [("2011-10-26", "FB"), ("2012-01-05", "FB"), ("2012-04-26", "META")])
    reports(sec, CIKS["FERG"], [("2011-12-01", "FERG"), ("2012-06-01", "FERG")])
    reports(sec, CIKS["GEN"], [("2011-02-01", "SYMC"), ("2011-08-01", "NLOK"), ("2012-02-01", "NLOK"),
                               ("2012-08-01", "GEN")])
    reports(sec, CIKS["CCC"], [("2010-12-01", "CCC"), ("2011-06-01", "CCC")])
    reports(sec, CIKS["GAP"], [("2010-12-01", "GPS"), ("2011-06-01", "GPS"), ("2011-12-01", "GPS"),
                               ("2012-06-01", "GAP"), ("2012-12-01", "GAP")])
    reports(sec, CIKS["SUN"], [("2012-06-22", "SUSP"), ("2012-11-14", "SUSP")])
    reports(sec, CIKS["PPP"], [("2011-05-01", "PPP"), ("2011-11-01", "PPP")])
    reports(sec, CIKS["BBB"], [("2005-03-01", "BBB"), ("2012-02-01", "BBB")])
    reports(sec, CIKS["WBD"], [("2012-02-01", "DISCA/DISCK"), ("2012-05-01", "WBD")])
    wiki = FakeWikipedia()
    wiki.pages["List_of_S%26P_500_companies"] = ("<table class='wikitable'><tr><th>Symbol</th><th>GICS Sector</th></tr>"
                                                  "<tr><td>AAA</td><td>Industrials</td></tr></table>")
    web = FakeWeb(yahoo, sec, wiki, gh)
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db",
                    research=ResearchConfig(contact="Jo Bloggs jo@example.com"))
    from tests.fakes.clock import FakeClock
    with open_context(config, None, FakeClock(NOW), web.transport) as ctx:
        yield ctx, yahoo, sec, web


def aliases(ctx):
    with research_context(ctx) as r:
        return {(a.old, a.new): a for a in TestbedRepo(r.conn).aliases()}


def members(ctx):
    with research_context(ctx) as r:
        return {m.ticker: m for m in TestbedRepo(r.conn).members()}


async def test_a_rename_is_accepted_when_sec_and_yahoo_agree_and_priced_from_the_new_ticker(env):
    ctx, *_ = env
    run = await build_testbed(ctx)
    assert run.stopped is None and not run.failures, run.summary
    fb = aliases(ctx)[("FB", "META")]
    assert fb.accepted and fb.effective_day == date(2012, 1, 3)
    assert "10-Q 2011-10-26 cover FB" in fb.evidence and "10-Q 2012-04-26 cover META" in fb.evidence
    assert "Yahoo: META priced on" in fb.evidence and "runs across 2012-01-03" in fb.evidence
    assert fb.priced_to == date(2012, 1, 3) + TAIL
    with open_testbed(ctx) as tb:
        got = tb.bars("FB", date(2011, 12, 28), date(2012, 1, 4))
        assert [(b.symbol, b.date) for b in got] == [("FB", d) for d in weekdays("2011-12-28", "2012-01-04")]
        assert tb.bars("FB", date(2012, 12, 1)) == [], "only to its last member day plus the tail"
        assert [b.date for b in tb.bars_between(date(2011, 12, 29), date(2011, 12, 30))["FB"]] == weekdays(
            "2011-12-29", "2011-12-30")


async def test_an_acquisition_replaced_the_same_day_is_kept_for_review_never_applied(env):
    ctx, *_ = env
    await build_testbed(ctx)
    ea = aliases(ctx)[("EA", "FERG")]
    assert not ea.accepted and ea.priced_to is None
    assert "cover FERG (not EA)" in ea.evidence, ea.evidence
    with open_testbed(ctx) as tb:
        assert tb.bars("EA", date(2010, 1, 1)) == []


async def test_a_chain_resolves_to_the_last_live_ticker(env):
    ctx, *_ = env
    await build_testbed(ctx)
    found = aliases(ctx)
    symc, nlok = found[("SYMC", "GEN")], found[("NLOK", "GEN")]
    assert symc.accepted and nlok.accepted and symc.effective_day == date(2011, 6, 1)
    assert "chain SYMC -> NLOK -> GEN" in symc.evidence
    with open_testbed(ctx) as tb:
        assert tb.bars("SYMC", date(2010, 6, 1), date(2010, 6, 1))[0].symbol == "SYMC"


async def test_a_company_renamed_after_it_left_is_found_by_name_and_checked_the_same_way(env):
    ctx, yahoo, *_ = env
    await build_testbed(ctx)
    found = aliases(ctx)
    assert not found[("GPS", "CCC")].accepted, "its same-day replacement is another company"
    gap = found[("GPS", "GAP")]
    assert gap.accepted and gap.effective_day == date(2012, 6, 1), gap.evidence
    assert "renamed after leaving" in gap.evidence and "GPS" in yahoo.searched
    assert company_name("The Gap, Inc.") == company_name("Gap Inc") == "gap"


async def test_coverage_shows_the_missing_share_before_and_after_the_map(env):
    ctx, *_ = env
    await build_testbed(ctx)
    c = coverage_of(ctx)
    y2010 = next(y for y in c.years if y.year == 2010)
    assert y2010.with_bars_mapped > y2010.with_bars
    assert y2010.missing_share_mapped < y2010.missing_share
    assert c.missing_share_mapped < c.missing_share
    assert c.renames_checked and c.aliases[0].accepted and not c.aliases[-1].accepted
    assert "FB" not in {u.ticker for u in c.under_priced}, "a mapped member is priced"
    assert "EA" in {u.ticker for u in c.under_priced}


async def test_sec_says_a_former_tickers_bars_are_another_companys_and_they_are_dropped(env):
    ctx, *_ = env
    run = await build_testbed(ctx)
    sun = members(ctx)["SUN"]
    assert sun.identity == "other" and sun.bars_note.startswith(REUSED) and "2012-06-22" in sun.bars_note
    with open_testbed(ctx) as tb:
        assert tb.bars("SUN", date(2000, 1, 1)) == []
    assert "SUN" in {u.ticker for u in coverage_of(ctx).under_priced}
    assert run.reused >= 1


async def test_sec_overrides_a_late_first_trade_day_for_a_former_member_that_is_the_same_company(env):
    ctx, *_ = env
    await build_testbed(ctx)
    bbb = members(ctx)["BBB"]
    assert bbb.identity == "same" and bbb.bars_note is None and bbb.cik == CIKS["BBB"]
    with open_testbed(ctx) as tb:
        assert tb.bars("BBB", date(2000, 1, 1))[0].date == date(2010, 9, 20)


async def test_a_late_sec_filer_whose_yahoo_series_runs_from_before_is_judged_by_its_reports(env):
    ctx, *_ = env
    await build_testbed(ctx)
    ppp = members(ctx)["PPP"]
    assert ppp.identity == "same" and ppp.bars_note is None, "a new holding company keeping its ticker and series"
    with open_testbed(ctx) as tb:
        assert tb.bars("PPP", date(2000, 1, 1))[0].date == date(2010, 1, 1)


async def test_two_members_priced_as_one_company_on_the_same_days_are_flagged(env):
    ctx, *_ = env
    await build_testbed(ctx)
    found = aliases(ctx)
    assert found[("DISCA", "WBD")].accepted and found[("DISCK", "WBD")].accepted
    pairs = {d.tickers: d for d in coverage_of(ctx).duplicates}
    assert ("DISCA", "DISCK") in pairs and "CIK 1437107" in pairs[("DISCA", "DISCK")].why


async def test_the_check_runs_once_after_the_first_full_build_and_again_only_when_asked(env):
    ctx, _, sec, _ = env
    await build_testbed(ctx)
    asked = len(sec.requests)
    again = await build_testbed(ctx)
    assert len(sec.requests) == asked and again.renames is None
    verified = await build_testbed(ctx, verify=True)
    assert len(sec.requests) > asked and verified.renames.startswith("6 renames accepted"), verified.renames


async def test_without_an_sec_contact_nothing_is_mapped_and_the_run_says_why(env, tmp_path):
    ctx, *_ = env
    ctx = replace(ctx, config=replace(ctx.config, research=ResearchConfig()))
    run = await build_testbed(ctx)
    assert run.renames.startswith("renames not checked: SEC needs a contact")
    assert aliases(ctx) == {}


async def test_sec_unavailable_maps_nothing_and_fails_the_run_so_a_retry_checks_again(env, monkeypatch):
    ctx, _, sec, _ = env
    monkeypatch.setattr(sec, "handle", lambda request: httpx.Response(503))
    run = await build_testbed(ctx)
    assert run.renames.startswith("renames not checked: SEC unavailable") and run.failures[-1].startswith("renames")
    assert aliases(ctx) == {}
    assert coverage_of(ctx).renames_checked is None, "the next build checks again"


def test_swap_candidates_pair_each_ticker_out_with_each_in_that_day():
    spans = [Span("FB", date(2010, 1, 4), date(2012, 1, 3)), Span("META", date(2012, 1, 3), None),
             Span("A", date(2010, 1, 4), date(2012, 4, 2)), Span("B", date(2010, 1, 4), date(2012, 4, 2)),
             Span("C", date(2012, 4, 2), None)]
    assert swap_candidates(spans, {"FB", "A", "B"}) == [
        Candidate("FB", "META", date(2012, 1, 3), True), Candidate("A", "C", date(2012, 4, 2), False),
        Candidate("B", "C", date(2012, 4, 2), False)]
    assert swap_candidates(spans, set()) == []
