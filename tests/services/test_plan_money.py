"""The fee calculator and contribution tests over stored total-return prices."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, PlanConfig
from oracle.market.bars import Bar
from oracle.market.dividends import Dividend
from oracle.services.context import open_context
from oracle.services.contributions_service import GrowthSeries, contribution_tests, growth_series
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fees_service import fee_drag, project, user_fees
from oracle.store.bars import BarRepo
from oracle.store.dividends import DividendRepo
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
STAMP = "2026-10-03T12:00:00+00:00"


def month_end_days(n: int) -> list[date]:
    return [date(2015 + i // 12, i % 12 + 1, 25) for i in range(n)]


def store(ctx, symbol: str, closes: list[float], dividends=()) -> None:
    days = month_end_days(len(closes))
    BarRepo(ctx.conn).upsert([Bar(symbol, d, c, c, c, c, None, 0, "GBP", "test") for d, c in zip(days, closes)], STAMP)
    repo = DividendRepo(ctx.conn)
    repo.replace(symbol, [Dividend(d, a, "GBP") for d, a in dividends], days[0], days[-1], "test", STAMP)
    repo.cover(symbol, days[0], days[-1], replace=True)


@pytest.fixture
def ctx(tmp_path):
    plan = PlanConfig(platform="percentage", percent_fee=0.25, flat_fee=4.99)
    with open_context(Config(db_path=tmp_path / "o.db", plan=plan), None, FakeClock(NOW)) as c:
        yield c


def test_user_fees_follow_the_plan_settings(ctx):
    assert user_fees(ctx).percent == pytest.approx(0.0025)
    ctx.config = replace(ctx.config, plan=replace(ctx.config.plan, platform="flat", percent_cap=45.0))
    assert user_fees(ctx).flat == pytest.approx(4.99) and user_fees(ctx).percent == 0


def test_fee_drag_compares_both_platforms_with_none_and_finds_the_crossover(ctx):
    d = fee_drag(ctx, 500.0, 20, 0.05)
    assert d.crossover == pytest.approx(23_952.0)
    names = [line.name for line in d.lines]
    assert names == ["none", "percentage", "flat"]
    none, pct, flat = d.lines
    assert none.drag == 0 and pct.drag > 0 and flat.drag > 0
    assert flat.fees == pytest.approx(4.99 * 240)
    assert d.contributed == pytest.approx(500.0 * 240)
    assert len(pct.yearly) == 20
    assert d.crossover_year == next(y for y, v in pct.yearly if v > 23_952.0)
    # Above the crossover for most of 20 years, flat ends ahead.
    assert flat.value > pct.value


def test_fee_drag_refuses_nonsense(ctx):
    with pytest.raises(InvalidQuery):
        fee_drag(ctx, 100.0, 0)
    with pytest.raises(InvalidQuery):
        fee_drag(ctx, -1.0, 10)


def test_growth_series_is_total_return_at_month_ends(ctx):
    store(ctx, "ACWI", [100.0] * 13, [(date(2015, 6, 25), 2.0)])
    g = growth_series(ctx)
    assert g.kind == "total return" and "proxy" in g.label
    assert g.points[-1][1] / g.points[0][1] == pytest.approx(1.02)


def test_too_little_history_is_said(ctx):
    store(ctx, "ACWI", [100.0] * 5)
    with pytest.raises(NoPrices):
        growth_series(ctx)


def test_contribution_tests_cover_every_start_and_say_a_missing_second_fund(ctx):
    store(ctx, "ACWI", [100.0 * 1.01 ** i for i in range(40)])
    t = contribution_tests(ctx, 100.0, years=2)
    assert len(t.starts) == 39 - 12 + 1 and t.lump.count == len(t.starts)
    assert t.lump.wins == 1.0
    assert t.window_count == 39 - 24 + 1
    assert t.mixes == () and any("CUKX.L" in n for n in t.notes)
    assert t.amount == 1200.0


def test_contribution_tests_rebalance_the_two_fund_mix(ctx):
    store(ctx, "ACWI", [100.0 * 1.02 ** i for i in range(30)])
    store(ctx, "CUKX.L", [100.0] * 30)
    t = contribution_tests(ctx, 100.0, years=1)
    assert [m.rule for m in t.mixes] == ["none", "yearly", "5-point bands"]
    none, yearly, bands = (m.result for m in t.mixes)
    assert none.weight > yearly.weight
    assert bands.rebalanced and yearly.rebalanced
    assert t.mix_span == (date(2015, 1, 25), date(2017, 6, 25))


def test_contribution_tests_take_any_growth_series(ctx):
    pts = tuple((d, 1.0) for d in month_end_days(14))
    t = contribution_tests(ctx, 50.0, years=1, growth=GrowthSeries("X", "fund X", "total return", pts), second=None)
    assert t.series.symbol == "X" and t.lump.count == 2
    assert t.cash.given_up == pytest.approx(0.0)


def test_the_projection_takes_an_extra_inflow(ctx):
    pts = [(d, 1.0) for d in month_end_days(4)]
    path = project(pts, [100.0] * 3, user_fees(ctx), extra=lambda i, paid: 25.0)
    assert path[-1].extra == pytest.approx(75.0)
    assert path[-1].value < 375.0


def test_the_drips_waiting_cash_earns_the_cash_rate(ctx):
    store(ctx, "ACWI", [100.0] * 14)
    plain = contribution_tests(ctx, 100.0, years=1, second=None)
    paid = contribution_tests(ctx, 100.0, years=1, second=None, cash_rate=0.05)
    assert paid.lump.median < plain.lump.median <= 0
