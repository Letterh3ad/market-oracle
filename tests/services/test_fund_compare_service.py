"""Fund comparison (boring-plan 02): proxies and real funds fetched into the research database only,
converted to sterling at each day's GBPUSD, dividends reinvested, the fund's TER taken, the real
fund tracked against its proxy, and the result kept."""

from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fund_compare_service import (FUNDS, KEY, compare_funds, fund_comparison, fund_series,
                                                  run_and_keep_funds, update_fund_data)
from oracle.services.signal_test_service import signal_tests
from oracle.services.testbed_service import research_context
from oracle.services.trend_service import KEY as TREND_KEY
from oracle.store.bars import BarRepo
from oracle.store.signal_tests import SignalTestRepo
from tests.fakes.clock import FakeClock
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2021, 1, 4, 12, 0, tzinfo=timezone.utc)
NY, LDN = "America/New_York", "Europe/London"
KNOWN = date(2020, 3, 16)


def weekdays(a: date, b: date) -> list[date]:
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


def bars(days, price) -> list[tuple]:
    return [(d, price(d), price(d), price(d), price(d), price(d), 1000) for d in days]


def rising(rate: float, at: float = 100.0):
    def price(d: date) -> float:
        return at * (1 + rate) ** ((d - date(2018, 1, 1)).days / 365.25)
    return price


def usd_per_gbp(d: date) -> float:
    return 1.2 if d == KNOWN else 1.25 if d < date(2020, 1, 1) else 1.30


def market(yahoo: FakeYahoo, end: date = date(2020, 12, 31)) -> None:
    proxies = {"ACWI": (date(2018, 1, 2), rising(0.08)), "URTH": (date(2018, 3, 1), rising(0.09)),
               "^SP500TR": (date(2018, 1, 2), rising(0.12)), "EWU": (date(2018, 1, 2), rising(0.03))}
    for symbol, (first, price) in proxies.items():
        yahoo.add(symbol, "USD", NY, bars(weekdays(first, end), price), kind="INDEX" if "^" in symbol else "ETF")
    yahoo.series["ACWI"].bars = [(d, *([60.0] * 5), 1000) if d == KNOWN else b
                                 for b in yahoo.series["ACWI"].bars for d in [b[0]]]
    yahoo.dividends["ACWI"] = [(date(2019, 6, 10), 1.0), (date(2020, 6, 10), 1.0)]
    for symbol, rate in (("VWRP.L", 0.08), ("SWDA.L", 0.09), ("VUAG.L", 0.12), ("VUKG.L", 0.03)):
        yahoo.add(symbol, "GBP", LDN, bars(weekdays(date(2019, 6, 3), end), rising(rate, 80.0)), kind="ETF")
    yahoo.add("GBPUSD=X", "USD", LDN, bars(weekdays(date(2003, 12, 1), end), usd_per_gbp), kind="CURRENCY")


@pytest.fixture
def env(tmp_path):
    yahoo = FakeYahoo()
    market(yahoo)
    web = FakeWeb(yahoo)
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW), web.transport) as ctx:
        yield ctx, yahoo


async def test_the_run_fetches_into_the_research_database_only_and_keeps_the_result(env):
    ctx, _ = env
    before = list(ctx.conn.iterdump())
    c = await run_and_keep_funds(ctx)
    assert list(ctx.conn.iterdump()) == before, "the live database is untouched"
    with research_context(ctx) as r:
        assert BarRepo(r.conn).last_date("VWRP.L") == date(2020, 12, 31)
    assert (c.start, c.end) == (date(2018, 3, 1), date(2020, 12, 31)), "URTH's first day starts the shared window"
    assert [row.key for row in c.rows] == ["all_world", "developed", "sp500", "uk", "uk_mix"]
    assert all(r.kind == "total return" for r in c.rows)
    assert all("proxy" in r.source for r in c.rows)
    assert [t.real for t in c.tracking] == ["VWRP.L", "SWDA.L", "VUAG.L", "VUKG.L"]
    kept = fund_comparison(ctx)
    assert kept is not None and kept.id and kept.rows == c.rows and kept.tracking == c.tracking and kept.fx == c.fx


async def test_a_usd_close_converts_at_that_days_gbpusd(env):
    ctx, _ = env
    await update_fund_data(ctx)
    s = dict(fund_series(ctx, "all_world").points)
    before, known = date(2020, 3, 13), KNOWN
    ratio = s[known] / s[before]
    gbp_before, gbp_known = rising(0.08)(before) / 1.30, 60.0 / 1.2
    fee = (0.9981 / 0.9968) ** (3 / 365.25)
    assert ratio == pytest.approx(gbp_known / gbp_before * fee)
    c = compare_funds(ctx)
    assert c.fx is not None and c.fx.symbol == "ACWI" and c.fx.currency == "USD"
    assert c.fx.close_base == pytest.approx(c.fx.close / c.fx.rate)


async def test_the_proxys_dividends_are_reinvested(env):
    ctx, yahoo = env
    await update_fund_data(ctx)
    s = dict(fund_series(ctx, "all_world").points)
    a, b = date(2019, 6, 7), date(2019, 6, 10)
    price = rising(0.08)
    fee = (0.9981 / 0.9968) ** (3 / 365.25)
    assert s[b] / s[a] == pytest.approx(price(b) / price(a) * (1 + 1.0 / price(b)) * fee)


async def test_the_ter_drag_is_the_return_the_charge_cost_and_the_tracking_is_printed_per_overlap(env):
    ctx, _ = env
    c = await run_and_keep_funds(ctx)
    rows = {r.key: r for r in c.rows}
    years = (c.end - c.start).days / 365.25
    sp = rows["sp500"]
    assert sp.ter_drag == pytest.approx((1 + sp.ret) / 0.9993 ** years - (1 + sp.ret))
    assert rows["uk_mix"].ter == pytest.approx(0.75 * 0.0019 + 0.25 * 0.0009)
    assert rows["uk_mix"].us_share == pytest.approx(0.75 * 0.62)
    assert rows["uk"].compared is False and rows["uk_mix"].source.startswith("proxy mix")
    vuag = next(t for t in c.tracking if t.real == "VUAG.L")
    assert vuag.start == date(2019, 9, 2) and vuag.end == date(2020, 12, 31), "a new line's first 90 days are skipped"
    assert vuag.difference == pytest.approx(vuag.real_return - vuag.model_return)
    assert abs(vuag.yearly_difference) < 0.06, "real and model rise at the same rate; FX moves apart only"


async def test_series_monthly_and_unknown_and_not_stored(env):
    ctx, _ = env
    with pytest.raises(NoPrices):
        fund_series(ctx, "all_world")
    with pytest.raises(InvalidQuery):
        fund_series(ctx, "nope")
    await update_fund_data(ctx)
    m = fund_series(ctx, "uk_mix", monthly=True)
    assert m.points[0][1] == pytest.approx(1.0) and m.kind == "total return"
    assert len(m.points) < 40 and m.ter == FUNDS["uk_mix"].ter


async def test_a_second_run_asks_from_the_last_stored_day(env):
    ctx, yahoo = env
    await update_fund_data(ctx)
    yahoo.charts.clear()
    await update_fund_data(ctx)
    asked = {s: p for s, p in yahoo.charts}
    assert "period1" in asked["ACWI"] and int(asked["ACWI"]["period1"]) > 1609000000


async def test_a_failed_fetch_is_a_note_when_days_are_stored(env):
    ctx, yahoo = env
    await update_fund_data(ctx)
    yahoo.fail["VUKG.L"] = 500
    c = await run_and_keep_funds(ctx)
    assert any("Could not update VUKG.L" in n for n in c.notes)


async def test_kept_plan_results_never_break_the_signal_tests_list(env):
    ctx, yahoo = env
    await run_and_keep_funds(ctx)
    with research_context(ctx) as r:
        SignalTestRepo(r.conn).add(TREND_KEY, "FAIL", "{}", "2021-01-04T00:00:00+00:00")
    assert signal_tests(ctx) == {} and signal_tests(ctx, train_only=True) == {}
    assert fund_comparison(ctx).rows[0].key == "all_world" and KEY == "fund_comparison"


async def test_the_job_runs_keeps_and_can_be_retried(env):
    from oracle.services.activity_service import JobRunner, retry, submit_plan_funds
    ctx, yahoo = env
    runner = JobRunner(ctx)
    yahoo.fail["ACWI"] = 500
    failed = await runner.wait(submit_plan_funds(ctx, runner).id)
    assert failed.state == "failed" and fund_comparison(ctx) is None
    del yahoo.fail["ACWI"]
    again = await runner.wait(retry(ctx, runner, failed.id).id)
    assert again.state == "done" and fund_comparison(ctx) is not None


async def test_bars_stored_without_their_dividends_are_fetched_again_from_the_start(env):
    ctx, yahoo = env
    from oracle.market.bars import Bar
    with research_context(ctx) as r:
        BarRepo(r.conn).upsert([Bar("ACWI", date(2020, 12, 30), 1.0, 1.0, 1.0, 1.0, 1.0, 0, "USD", "yahoo")], "x")
    await update_fund_data(ctx)
    asked = [p for s, p in yahoo.charts if s == "ACWI"]
    assert int(asked[0]["period1"]) < 500_000_000, "from 1985: the stored day had no dividends asked over it"
    assert fund_series(ctx, "all_world").kind == "total return"
    assert fund_series(ctx, "all_world").points[0][0] == date(2018, 1, 2)
