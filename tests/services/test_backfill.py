from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oracle.config import Config
from oracle.services.backfill_service import backfill_status, backfill_universe
from oracle.services.context import open_context
from oracle.services.errors import UniverseNotLoaded
from oracle.services.universe_service import refresh_universe, resolve_symbol, search_universe
from oracle.store.universe import UniverseRepo
from oracle.universe.indexes import BY_CODE, IndexSpec
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, bz2_csv, equity
from tests.fakes.web import FakeWeb
from tests.fakes.wikipedia import FakeWikipedia, members_table
from tests.fakes.yahoo import FakeYahoo, bar, profile

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
KR = IndexSpec("kr", "Korea 3", "Korea_3", ".KS", pad=6)
US = IndexSpec("us", "US 2", "US_2", "")
FIXTURES = Path(__file__).parent.parent / "fixtures" / "indexes"

UNIVERSE = [
    equity("LGLG.F", "LG Electronics Inc.", "South Korea", "FRA", currency="EUR", isin="US50186Q2021",
           sector="Consumer Discretionary", industry_group="Consumer Durables & Apparel",
           industry="Household Durables", market_cap="Large Cap"),
    equity("008110.KS", "Samsung C&T Corporation", "South Korea", "KSC", currency="KRW", market="KOSPI Stock Market",
           sector="Industrials", market_cap="Micro Cap"),
    equity("005930.KS", "Samsung Electronics Co., Ltd.", "South Korea", "KSC", currency="KRW",
           market="KOSPI Stock Market", sector="Information Technology", market_cap="Mega Cap"),
    equity("AAPL", "Apple Inc.", currency="USD", market_cap="Micro Cap", sector="Information Technology"),
]


@pytest.fixture
def fdb():
    return FakeFinanceDatabase(UNIVERSE)


@pytest.fixture
def wiki():
    w = FakeWikipedia()
    w.pages["Korea_3"] = members_table(("066570", "LG Electronics"), ("373220", "LG Energy Solution"),
                                       ("028260", "Samsung C&T"), ("005930", "Samsung Electronics"))
    w.pages["US_2"] = members_table(("AAPL", "Apple Inc."), ("TSLA", "Tesla, Inc."))
    return w


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("USDKRW=X", "KRW", "Asia/Seoul", [bar("2026-09-23", 1390.0)])
    y.summaries["066570.KS"] = profile("LG Electronics Inc.", "KSC", "KRW", 37.5e12, "South Korea", "Technology",
                                       "Consumer Electronics")
    y.summaries["373220.KS"] = profile("LG Energy Solution, Ltd.", "KSC", "KRW", 82.3e12, "South Korea", "Industrials",
                                       "Electrical Equipment & Parts")
    y.summaries["028260.KS"] = profile("Samsung C&T Corporation", "KSC", "KRW", 60.1e12, "South Korea", "Industrials",
                                       "Engineering & Construction")
    y.summaries["AAPL"] = profile("Apple Inc.", "NMS", "USD", 3.9e12, "United States", "Technology")
    return y


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def web(fdb, wiki, yahoo):
    return FakeWeb(fdb, wiki, yahoo)


@pytest.fixture
def ctx(tmp_path, web, clock):
    with open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport) as c:
        yield c


@pytest.fixture
def asked(web):
    """Every symbol Yahoo was asked to profile, in order."""
    return lambda: [r.url.path.rsplit("/", 1)[1] for r in web.requests if "/quoteSummary/" in r.url.path]


async def run(ctx, *indexes, **kw):
    return await backfill_universe(ctx, indexes=indexes or (KR, US), **kw)


async def test_it_needs_a_loaded_universe(ctx):
    with pytest.raises(UniverseNotLoaded):
        await run(ctx)


async def test_missing_members_are_added_as_home_lines_grouped_with_their_company(ctx):
    await refresh_universe(ctx)
    report = await run(ctx)
    lge = resolve_symbol(ctx, "066570.KS")
    assert lge.company_key == resolve_symbol(ctx, "LGLG.F").company_key
    assert (lge.symbol, lge.name, lge.market_cap) == ("066570.KS", "LG Electronics Inc.", "Large Cap")
    lges = resolve_symbol(ctx, "373220.KS")
    assert (lges.name, lges.currency, lges.market_cap) == ("LG Energy Solution, Ltd.", "KRW", "Large Cap")
    row = {x.symbol: x for x in UniverseRepo(ctx.conn).all_listings()}["066570.KS"]
    assert (row.source, row.is_primary, row.exchange, row.market) == ("backfill", True, "KSC", "KOSPI Stock Market")
    # The company's own classification, not Yahoo's, so screens read one vocabulary.
    assert (row.sector, row.industry) == ("Consumer Discretionary", "Household Durables")
    kr = next(c for c in report.indexes if c.code == "kr")
    assert (kr.members, kr.present, kr.added, kr.corrected, kr.failed, kr.waiting) == (4, 1, 3, 1, 0, 0)
    assert {(c.symbol, c.kind) for c in report.changes} >= {("066570.KS", "added"), ("373220.KS", "added")}


async def test_a_company_under_the_wrong_code_is_corrected(ctx, yahoo):
    await refresh_universe(ctx)
    report = await run(ctx, KR)
    rows = {x.symbol: x for x in UniverseRepo(ctx.conn).all_listings()}
    assert rows["008110.KS"].delisted
    assert rows["028260.KS"].is_primary and rows["028260.KS"].market_cap == "Large Cap"
    assert rows["028260.KS"].company_key == rows["008110.KS"].company_key
    fix = next(c for c in report.changes if c.symbol == "008110.KS")
    assert fix.kind == "corrected" and "028260.KS" in fix.note
    assert [h.symbol for h in search_universe(ctx, "Samsung C&T")] == ["028260.KS"]


async def test_a_wrong_code_yahoo_names_otherwise_is_renamed(ctx, yahoo):
    yahoo.summaries["008110.KS"] = profile("Daidong Electronics Co., Ltd.", "KSC", "KRW", 30e9, "South Korea")
    await refresh_universe(ctx)
    await run(ctx, KR)
    rows = {x.symbol: x for x in UniverseRepo(ctx.conn).all_listings()}
    assert (rows["008110.KS"].name, rows["008110.KS"].delisted) == ("Daidong Electronics Co., Ltd.", False)
    assert rows["008110.KS"].company_key != rows["028260.KS"].company_key


async def test_tesla_is_never_looked_up_or_added(ctx, asked):
    await refresh_universe(ctx)
    report = await run(ctx, US)
    assert "TSLA" not in asked()
    assert UniverseRepo(ctx.conn).resolve("TSLA") is None
    us = report.indexes[0]
    assert (us.members, us.present, us.failed) == (2, 1, 1)


async def test_funds_and_receipts_are_refused_once_and_not_asked_again(ctx, wiki, yahoo, asked):
    wiki.pages["US_2"] = members_table(("SPY", "SPDR S&P 500"), ("SSNLF", "Samsung GDR"))
    yahoo.summaries["SPY"] = profile("SPDR S&P 500 ETF Trust", "PCX", "USD", None, quote_type="ETF")
    yahoo.summaries["SSNLF"] = profile("Samsung Electronics GDR", "PNK", "USD", 1e9, "South Korea")
    await refresh_universe(ctx)
    report = await run(ctx, US)
    assert {c.symbol: c.note for c in report.changes if c.kind == "failed"} == {
        "SPY": "not a company's shares (ETF)", "SSNLF": "a depositary receipt"}
    await run(ctx, US)
    assert asked().count("SPY") == 1


async def test_a_refresh_keeps_backfilled_lines_until_financedatabase_has_them(ctx, fdb):
    await refresh_universe(ctx)
    await run(ctx, KR)
    await refresh_universe(ctx)
    rows = {x.symbol: x for x in UniverseRepo(ctx.conn).all_listings()}
    assert rows["373220.KS"].source == "backfill" and rows["008110.KS"].delisted
    assert rows["066570.KS"].company_key == rows["LGLG.F"].company_key and rows["066570.KS"].is_primary
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([*UNIVERSE, equity("373220.KS", "LG Energy Solution Ltd", "South Korea", "KSC")])
    await refresh_universe(ctx)
    lges = [x for x in UniverseRepo(ctx.conn).all_listings() if x.symbol.upper() == "373220.KS"]
    assert [(x.name, x.source) for x in lges] == [("LG Energy Solution Ltd", None)]


async def test_runs_are_bounded_and_resume_where_they_stopped(ctx, asked):
    await refresh_universe(ctx)
    first = await run(ctx, KR, max_lookups=1)
    assert first.lookups == 1 and first.indexes[0].waiting == 2
    second = await run(ctx, KR)
    assert second.indexes[0].waiting == 0
    assert len(asked()) == len(set(asked()))


async def test_a_rate_limited_yahoo_stops_the_run_and_keeps_what_was_added(ctx, yahoo):
    yahoo.fail["373220.KS"] = 429
    await refresh_universe(ctx)
    report = await run(ctx, KR)
    assert "429" in report.stopped
    assert resolve_symbol(ctx, "066570.KS").symbol == "066570.KS"
    assert UniverseRepo(ctx.conn).resolve("373220.KS") is None
    assert report.indexes[0].waiting == 2


async def test_index_lists_are_cached_for_a_week(ctx, wiki, clock):
    await refresh_universe(ctx)
    await run(ctx)
    await run(ctx)
    assert len(wiki.requests) == 2
    clock.advance(timedelta(days=8).total_seconds())
    await run(ctx)
    assert len(wiki.requests) == 4


async def test_an_unreadable_page_keeps_the_cached_list_and_says_so(ctx, wiki, clock):
    await refresh_universe(ctx)
    await run(ctx, KR)
    wiki.pages["Korea_3"] = "<p>moved</p>"
    clock.advance(timedelta(days=8).total_seconds())
    report = await run(ctx, KR)
    assert report.indexes[0].members == 4 and "no member table" in report.indexes[0].error


async def test_a_member_whose_band_is_far_off_yahoos_is_corrected(ctx):
    await refresh_universe(ctx)
    report = await run(ctx, US)
    assert resolve_symbol(ctx, "AAPL").market_cap == "Mega Cap"
    assert next(c for c in report.changes if c.symbol == "AAPL").kind == "corrected"


async def test_an_isin_from_the_page_groups_the_new_line(ctx, fdb, wiki, yahoo):
    fdb.files["equities.bz2"] = bz2_csv([equity("ENL.F", "ENEL S.p.A. (Registered)", "Italy", "FRA",
                                               isin="IT0003128367")])
    wiki.pages["MIB_1"] = ("<table class='wikitable'><tr><th>Ticker</th><th>Company</th><th>ISIN</th></tr>"
                           "<tr><td>ENEL.MI</td><td>Enel</td><td>IT0003128367</td></tr></table>")
    yahoo.add("USDEUR=X", "EUR", "Europe/Rome", [bar("2026-09-23", 0.9)])
    yahoo.summaries["ENEL.MI"] = profile("Enel SpA", "MIL", "EUR", 70e9, "Italy", "Utilities")
    await refresh_universe(ctx)
    await run(ctx, IndexSpec("mib", "MIB 1", "MIB_1", ""))
    assert resolve_symbol(ctx, "ENEL.MI").company_key == resolve_symbol(ctx, "ENL.F").company_key


async def test_status_reports_counts_without_running(ctx):
    await refresh_universe(ctx)
    await run(ctx, KR)
    assert [(c.code, c.members, c.added) for c in backfill_status(ctx, indexes=(KR, US))] == [
        ("kr", 4, 3), ("us", 0, 0)]


async def test_the_live_kospi_page_adds_the_lg_home_lines(ctx, wiki, yahoo):
    yahoo.summaries["051910.KS"] = profile("LG Chem, Ltd.", "KSC", "KRW", 25e12, "South Korea", "Basic Materials")
    yahoo.summaries["003550.KS"] = profile("LG Corp.", "KSC", "KRW", 13e12, "South Korea", "Industrials")
    wiki.pages["KOSPI_200"] = (FIXTURES / "kospi200.html").read_text(encoding="utf-8")
    await refresh_universe(ctx)
    report = await run(ctx, BY_CODE["kospi200"], max_lookups=400)
    added = {c.symbol for c in report.changes if c.kind == "added"}
    assert {"066570.KS", "373220.KS", "051910.KS", "003550.KS", "028260.KS"} <= added
    assert report.indexes[0].members == 200
