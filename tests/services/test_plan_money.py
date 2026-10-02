"""The fee calculator and contribution tests over stored total-return prices."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, PlanConfig
from oracle.services.context import open_context
from oracle.services.contributions_service import contribution_tests, plan_series
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fees_service import fee_drag, project, user_fees
from oracle.services.fund_compare_service import FundSeries, update_fund_data
from tests.fakes.clock import FakeClock
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo
from tests.services.test_fund_compare_service import market

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
STAMP = "2026-10-03T12:00:00+00:00"


def month_end_days(n: int) -> list[date]:
    return [date(2015 + i // 12, i % 12 + 1, 25) for i in range(n)]


PLAN = PlanConfig(platform="percentage", percent_fee=0.25, flat_fee=4.99)


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db", plan=PLAN), None,
                      FakeClock(NOW)) as c:
        yield c


async def seed(tmp_path, end: date = date(2020, 12, 31)) -> None:
    """Fund comparison's proxies (2018-01-02 on, EWU for the UK fund) fetched into the research DB."""
    yahoo = FakeYahoo()
    market(yahoo, end)
    config = Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db")
    with open_context(config, None, FakeClock(datetime(2021, 1, 4, tzinfo=timezone.utc)), FakeWeb(yahoo).transport) as c:
        await update_fund_data(c)


def fund(n: int, step: float = 1.0) -> FundSeries:
    return FundSeries("x", "Fund X", "proxy: X", "total return", 0.0,
                      tuple((d, step ** i) for i, d in enumerate(month_end_days(n))))


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


async def test_the_series_is_fund_comparisons_cut_to_whole_months(ctx, tmp_path):
    await seed(tmp_path)
    s = plan_series(ctx, "all_world")
    assert s.points[0][0] == date(2018, 1, 31), "the first day (2018-01-02) inside January is dropped"
    assert s.points[-1][0] == date(2020, 12, 31) and "ACWI" in s.source and s.kind == "total return"


async def test_the_running_month_is_not_a_month_end(tmp_path):
    await seed(tmp_path, end=date(2020, 12, 2))
    config = Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db")
    with open_context(config, None, FakeClock(datetime(2020, 12, 3, tzinfo=timezone.utc))) as c:
        assert plan_series(c, "all_world").points[-1][0] == date(2020, 11, 30)


def test_no_history_says_which_job_fetches_it(ctx):
    with pytest.raises(NoPrices, match="fund comparison"):
        plan_series(ctx, "all_world")
    with pytest.raises(InvalidQuery):
        plan_series(ctx, "nope")


async def test_contribution_tests_run_on_fund_comparisons_series_and_say_their_span(ctx, tmp_path):
    await seed(tmp_path)
    t = contribution_tests(ctx, 100.0, years=2)
    assert (t.first, t.last, t.months) == (date(2018, 1, 31), date(2020, 12, 31), 35)
    assert len(t.starts) == 35 - 12 + 1 and t.lump.count == len(t.starts)
    assert t.window_count == 35 - 24 + 1 and t.window_span == (date(2018, 1, 31), date(2018, 12, 31))
    assert t.second.key == "uk" and [m.rule for m in t.mixes] == ["none", "yearly", "5-point bands"]
    assert t.mix_span == (date(2018, 1, 31), date(2020, 12, 31))
    assert t.amount == 1200.0 and any("TER" in n for n in t.notes)


def test_a_missing_second_fund_is_said_not_raised(ctx):
    t = contribution_tests(ctx, 100.0, years=1, growth=fund(30, 1.01))
    assert t.mixes == () and any("No rebalancing test" in n and "fund comparison" in n for n in t.notes)


def test_contribution_tests_rebalance_the_two_fund_mix(ctx):
    t = contribution_tests(ctx, 100.0, years=1, growth=fund(30, 1.02), second=fund(30))
    none, yearly, bands = (m.result for m in t.mixes)
    assert none.weight > yearly.weight
    assert bands.rebalanced and yearly.rebalanced


def test_contribution_tests_take_any_growth_series(ctx):
    t = contribution_tests(ctx, 50.0, years=1, growth=fund(14), second=None)
    assert t.series.key == "x" and t.lump.count == 2
    assert t.cash.given_up == pytest.approx(0.0)


def test_the_projection_takes_an_extra_inflow(ctx):
    pts = [(d, 1.0) for d in month_end_days(4)]
    path = project(pts, [100.0] * 3, user_fees(ctx), extra=lambda i, paid: 25.0)
    assert path[-1].extra == pytest.approx(75.0)
    assert path[-1].value < 375.0


def test_the_drips_waiting_cash_earns_the_cash_rate(ctx):
    plain = contribution_tests(ctx, 100.0, years=1, growth=fund(14), second=None)
    paid = contribution_tests(ctx, 100.0, years=1, growth=fund(14), second=None, cash_rate=0.05)
    assert paid.lump.median < plain.lump.median <= 0


@pytest.mark.parametrize("kwargs", [{"amount": 0.0}, {"amount": -5.0}, {"cash_rate": -1.0}, {"monthly": 0.0}])
def test_nonsense_amounts_are_refused(ctx, kwargs):
    with pytest.raises(InvalidQuery):
        contribution_tests(ctx, **{"monthly": 100.0, **kwargs}, growth=fund(14), second=None)


def test_fees_eating_the_whole_drip_are_refused_not_divided_by(ctx):
    ctx.config = replace(ctx.config, plan=replace(PLAN, dealing_fee=10.0))
    with pytest.raises(InvalidQuery, match="drip tranche"):
        contribution_tests(ctx, 100.0, amount=60.0, years=1, growth=fund(14), second=None)
