from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config
from oracle.market.bars import Bar
from oracle.services.context import open_context
from oracle.services.events_service import earnings_study
from oracle.services.universe_service import refresh_universe
from oracle.store.bars import BarRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.sec import FakeSec
from tests.fakes.web import FakeWeb

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
APPLE = "US0378331005"
UNIVERSE = [
    equity("AAPL", "Apple Inc.", isin=APPLE, currency="USD"),
    equity("APC.DE", "Apple Inc.", exchange="GER", isin=APPLE, currency="EUR"),
    equity("BP.L", "BP p.l.c.", "United Kingdom", "LSE", currency="GBP"),
]
CIK = 320193
WEEKDAYS = [d for d in (date(2026, 1, 2) + timedelta(n) for n in range(260)) if d.weekday() < 5 and d <= date(2026, 9, 18)]


def release(day, clock, items="2.02,9.01"):
    return ("8-K", day, f"0000320193-26-{day[5:7]}{day[8:]}", "a.htm", "", items, f"{day}T{clock}:00.000Z")


@pytest.fixture
def sec():
    s = FakeSec()
    s.companies["AAPL"] = (CIK, "Apple Inc.")
    s.filings[CIK] = [
        release("2026-09-10", "16:30"),
        release("2026-07-30", "16:31"),
        release("2026-06-02", "16:00", items="5.07"),
        release("2026-05-01", "06:45"),
        release("2026-01-29", "16:30"),
    ]
    return s


@pytest.fixture
def web(sec):
    return FakeWeb(FakeFinanceDatabase(UNIVERSE), sec)


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
async def ctx(tmp_path, web, clock):
    config = Config(db_path=tmp_path / "o.db")
    config = replace(config, research=replace(config.research, contact="Faye faye@example.com"))
    with open_context(config, None, clock, web.transport) as c:
        await refresh_universe(c)
        yield c


def closes(symbol, moves, currency="USD", start=100.0):
    """Weekday bars from a flat start, each (day, ratio) moving that day's close and after."""
    level, bars = start, []
    for d in WEEKDAYS:
        level *= moves.get(d.isoformat(), 1.0)
        bars.append(Bar(symbol, d, None, None, None, level, None, None, currency, "yahoo"))
    return bars


def store(ctx, bars):
    BarRepo(ctx.conn).upsert(bars, NOW.isoformat())


@pytest.fixture
def priced(ctx):
    store(ctx, closes("AAPL", {
        "2026-02-03": 1 / 3,  # a 3-for-1 split the stored bars were never re-based for
        "2026-05-01": 0.98,
        "2026-07-31": 1.05,
        "2026-08-10": 1.10,
        "2026-09-11": 1.02,
    }))
    store(ctx, closes("VWRL.L", {"2026-07-31": 1.01}, currency="GBP", start=50.0))
    return ctx


async def test_moves_after_each_release_raw_and_against_the_benchmark(priced):
    study = await earnings_study(priced, "AAPL")
    assert (study.source, study.benchmark, study.note) == ("SEC 8-K item 2.02", "VWRL.L", None)
    by_day = {e.day.isoformat(): e for e in study.events}
    assert list(by_day) == ["2026-09-10", "2026-07-30", "2026-05-01", "2026-01-29"]

    july = by_day["2026-07-30"]
    assert july.base == date(2026, 7, 30)
    one, five, twenty = july.windows
    assert (one.days, one.move, one.excess) == (1, pytest.approx(0.05), pytest.approx(0.04))
    assert five.move == pytest.approx(0.05)
    assert twenty.move == pytest.approx(1.05 * 1.10 - 1)


async def test_a_release_before_the_open_is_measured_from_the_close_before(priced):
    may = next(e for e in (await earnings_study(priced, "AAPL")).events if e.day == date(2026, 5, 1))
    assert may.before_open is True
    assert may.base == date(2026, 4, 30)
    assert may.windows[0].move == pytest.approx(-0.02)


async def test_a_split_inside_the_window_skips_the_event(priced):
    jan = next(e for e in (await earnings_study(priced, "AAPL")).events if e.day == date(2026, 1, 29))
    assert jan.note == "price series break, skipped"
    assert all(w.move is None for w in jan.windows)


async def test_windows_past_the_last_stored_close_are_left_open(priced):
    sept = (await earnings_study(priced, "AAPL")).events[0]
    assert sept.windows[0].move == pytest.approx(0.02)
    assert sept.windows[1].move == pytest.approx(0.02)
    assert sept.windows[2].move is None


async def test_the_summary_counts_measured_events(priced):
    study = await earnings_study(priced, "AAPL")
    one = study.summaries[0]
    assert (one.days, one.n, one.up, one.median) == (1, 3, 2, pytest.approx(0.02))
    assert study.headline == "after the last 3 earnings: median +2.0% next day, 2 of 3 up"
    assert study.summaries[2].n == 2


async def test_dates_are_stored_and_refetched_only_after_a_week(priced, web, clock):
    await earnings_study(priced, "AAPL")
    calls = len(web.requests_to("data.sec.gov"))
    await earnings_study(priced, "AAPL")
    stored = await earnings_study(priced, "AAPL", refresh=False)
    assert len(web.requests_to("data.sec.gov")) == calls
    assert len(stored.events) == 4
    clock.advance(8 * 86400)
    await earnings_study(priced, "AAPL")
    assert len(web.requests_to("data.sec.gov")) == calls + 1


async def test_a_failed_fetch_keeps_the_stored_dates_and_says_why(priced, sec, clock):
    await earnings_study(priced, "AAPL")
    clock.advance(8 * 86400)
    sec.filings.pop(CIK)
    study = await earnings_study(priced, "AAPL")
    assert len(study.events) == 4
    assert "could not refresh" in study.note


async def test_a_non_us_company_says_there_is_no_source(ctx, web):
    study = await earnings_study(ctx, "BP.L")
    assert study.events == () and study.source is None
    assert "no earnings dates source" in study.note
    assert web.requests_to("data.sec.gov") == []


async def test_another_listing_of_a_us_company_uses_the_main_listings_filings(ctx):
    store(ctx, closes("APC.DE", {"2026-07-31": 1.04}, currency="EUR"))
    study = await earnings_study(ctx, "APC.DE")
    july = next(e for e in study.events if e.day == date(2026, 7, 30))
    assert july.windows[0].move == pytest.approx(0.04)


async def test_without_a_contact_sec_is_not_asked(ctx, web):
    ctx.config = replace(ctx.config, research=replace(ctx.config.research, contact=None))
    study = await earnings_study(ctx, "AAPL")
    assert "contact" in study.note
    assert web.requests_to("data.sec.gov") == []


async def test_without_benchmark_bars_only_raw_moves_show(ctx):
    store(ctx, closes("AAPL", {"2026-07-31": 1.05}))
    study = await earnings_study(ctx, "AAPL")
    july = next(e for e in study.events if e.day == date(2026, 7, 30))
    assert study.benchmark is None
    assert (july.windows[0].move, july.windows[0].excess) == (pytest.approx(0.05), None)


async def test_no_stored_bars_says_so_per_event(ctx):
    study = await earnings_study(ctx, "AAPL")
    assert {e.note for e in study.events} == {"no stored close near the date"}
    assert study.headline is None
