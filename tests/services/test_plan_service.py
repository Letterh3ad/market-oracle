"""The "your plan" summary: the fund pick, wrapper, platform, monthly amount and overlay, each with
its reason, and what is missing instead of an error."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, PlanConfig
from oracle.services import plan_service
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.fund_compare_service import FundComparison, FundRow
from oracle.services.plan_service import check_choice, default_pick, plan_summary
from oracle.services.settings_service import set_plan
from oracle.services.trend_service import TrendLine, TrendTest, TrendWindow
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def row(key: str, cagr: float, fall: float, us: float, compared: bool = True) -> FundRow:
    return FundRow(key, key, f"proxy: {key}", "total return", 1.0, cagr, fall, 0.002, 0.1, us, compared,
                   date(2008, 3, 28), 1.0, cagr, fall, ())


ROWS = (row("all_world", 0.1268, -0.273, 0.62), row("developed", 0.1328, -0.278, 0.72),
        row("sp500", 0.1596, -0.261, 1.0), row("uk", 0.0793, -0.36, 0.0, compared=False),
        row("uk_mix", 0.1157, -0.288, 0.46))


def comparison(rows=ROWS) -> FundComparison:
    return FundComparison("GBP", date(2012, 1, 12), date(2026, 10, 1), rows, (), None, "2026-10-03",
                          "2026-10-03T10:00:00+00:00", ())


def trend(passed: bool) -> TrendTest:
    line = TrendLine("x", "x", 1.0, 0.08, -0.26, 0.2, ())
    w = TrendWindow("confirm", date(2008, 1, 2), date(2026, 10, 1), line, line, 31, 0.498 if not passed else 0.4,
                    0.0331 if not passed else 0.005, "PASS" if passed else "FAIL", ())
    return TrendTest("^SP500TR", "S&P 500", "rule", "0", 0.001, w, w, "PASS" if passed else "FAIL", passed,
                     date(1988, 1, 4), date(2026, 10, 1), "2026-10-02T10:00:00+00:00", ())


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None,
                      FakeClock(NOW)) as c:
        c.config_source = tmp_path / "oracle.toml"
        yield c


@pytest.fixture
def kept(monkeypatch):
    """Stand-ins for the kept fund comparison and trend test; set `.funds`/`.trend` per test."""
    class Kept:
        funds = comparison()
        trend = trend(False)
    monkeypatch.setattr(plan_service, "fund_comparison", lambda ctx: Kept.funds)
    monkeypatch.setattr(plan_service, "trend_tests", lambda ctx: Kept.trend)
    return Kept


def lines(summary) -> dict:
    return {line.name: line for line in summary.lines}


def test_all_world_stays_the_pick_when_only_more_us_heavy_funds_beat_it():
    key, reason = default_pick(ROWS)
    assert key == "all_world"
    assert "S&P 500" in reason and "100% US" in reason and "62% US" in reason


def test_a_fund_no_more_us_heavy_that_beats_all_world_on_return_and_fall_takes_the_pick():
    better = row("uk_mix", 0.14, -0.20, 0.46)
    key, reason = default_pick((ROWS[0], better, ROWS[2]))
    assert key == "uk_mix" and "beat" in reason


def test_beating_on_return_alone_is_not_enough():
    assert default_pick((ROWS[0], row("uk_mix", 0.14, -0.30, 0.46)))[0] == "all_world"


def test_nothing_run_or_set_lists_what_is_missing_instead_of_failing(ctx, monkeypatch):
    monkeypatch.setattr(plan_service, "fund_comparison", lambda ctx: None)
    monkeypatch.setattr(plan_service, "trend_tests", lambda ctx: None)
    s = plan_summary(ctx)
    got = lines(s)
    assert s.fund == "all_world" and not s.picked and s.monthly == 0 and s.overlay is False
    assert [m.step for m in s.missing] == ["funds", "platform", "monthly", "trend", "rule"]
    assert "Compare funds" in s.missing[0].text
    assert not got["fund"].known and not got["overlay"].known and got["wrapper"].known
    assert got["overlay"].value.startswith("No")


def test_a_failed_trend_test_says_no_with_its_numbers(ctx, kept):
    got = lines(plan_summary(ctx))
    assert got["overlay"].value == "No" and got["overlay"].known
    assert "50%" in got["overlay"].reason and "3.31 points" in got["overlay"].reason
    kept.trend = trend(True)
    s = plan_summary(ctx)
    assert s.overlay is True and lines(s)["overlay"].value == "Yes"


def test_the_users_pick_wins_and_the_numbers_default_is_still_said(ctx, kept):
    ctx.config = replace(ctx.config, plan=PlanConfig(fund="sp500"))
    s = plan_summary(ctx)
    assert s.fund == "sp500" and s.picked and s.default_fund == "all_world"
    assert "Your pick" in lines(s)["fund"].reason and "All-world" in lines(s)["fund"].reason


def test_an_unknown_saved_fund_falls_back_to_the_default_and_says_so(ctx, kept):
    ctx.config = replace(ctx.config, plan=PlanConfig(fund="gold"))
    s = plan_summary(ctx)
    assert s.fund == "all_world" and not s.picked and "fund" in [m.step for m in s.missing]


def test_platform_and_monthly_with_fees_set_give_the_crossover_and_the_allowance(ctx, kept):
    ctx.config = replace(ctx.config, plan=PlanConfig(percent_fee=0.25, flat_fee=4.99, monthly=500.0))
    s = plan_summary(ctx)
    got = lines(s)
    assert [m.step for m in s.missing] == ["rule"] and s.monthly == 500.0
    assert "£23,952" in got["platform"].reason and "year 4" in got["platform"].reason
    assert got["monthly"].value == "£500 a month"
    assert "£6,000 a year of the £20,000" in got["wrapper"].reason


def test_check_choice_checks_the_fund_and_trims_the_rule():
    assert check_choice({"fund": " SP500 ", "rule": "  Hold. "}) == {"fund": "sp500", "rule": "Hold."}
    assert check_choice({"fund": "default"}) == {"fund": ""}
    for bad in ("gold", "uk"):
        with pytest.raises(InvalidQuery, match=bad):
            check_choice({"fund": bad})


def test_set_plan_with_some_keys_keeps_the_rest_in_memory(ctx):
    set_plan(ctx, {"platform": "flat", "flat_fee": 4.99})
    set_plan(ctx, {"dealing_fee": 1.5})
    assert ctx.config.plan.platform == "flat" and ctx.config.plan.flat_fee == 4.99 and ctx.config.plan.dealing_fee == 1.5
