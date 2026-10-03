"""Where the next pound goes: the workplace pension with its relief and employer money, the ISA, and
the Lifetime ISA when turned on, through the one projection (`project`, its `extra` hook)."""

import tomllib
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.broker.projection import project, steady
from oracle.broker.wrappers import nest_fees
from oracle.config import Config, PlanConfig, WrappersConfig
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.wrappers_service import PER, save_wrappers, wrapper_comparison
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
BASIC = WrappersConfig(salary=30_000, employee_pct=5, employer_pct=3, born=1996, needed_before=200)


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None,
                      FakeClock(NOW)) as c:
        c.config_source = tmp_path / "oracle.toml"
        yield c


def use(ctx, wrappers=BASIC, monthly=500.0, **plan):
    ctx.config = replace(ctx.config, wrappers=wrappers, plan=PlanConfig(monthly=monthly, **plan))


def rows(c) -> dict:
    return {r.key: r for r in c.rows}


def test_nothing_entered_lists_what_is_missing_and_assumes_nothing(ctx):
    c = wrapper_comparison(ctx)
    assert [m.step for m in c.missing] == ["salary", "born", "needed", "monthly"]
    assert c.rows == () and c.steps == () and c.workplace is None and c.lisa is False


def test_the_workplace_pension_month_uses_relief_and_employer_money(ctx):
    use(ctx)
    w = wrapper_comparison(ctx).workplace
    qualifying = (30_000 - 6_240) / 12
    assert w.gross == pytest.approx(qualifying * 0.05) and w.employer == pytest.approx(qualifying * 0.03)
    assert w.relief == pytest.approx(qualifying * 0.01) and w.cost == pytest.approx(qualifying * 0.04)


def test_rows_are_100_a_month_of_take_home_to_the_pension_age_through_the_one_projection(ctx):
    use(ctx)
    c = wrapper_comparison(ctx, growth=0.05)
    assert (c.age, c.access_age, c.years, c.paid_years) == (30, 57, 27, 27) and list(rows(c)) == ["isa", "pension"]
    months = 27 * 12
    isa, pension = rows(c)["isa"], rows(c)["pension"]
    series = steady(0.05, months, date(2026, 10, 3))
    assert isa.paid_in == pytest.approx(PER * months) and isa.added == 0
    assert isa.pot == pytest.approx(project(series, [PER] * months).pop().value) and isa.spendable == isa.pot
    # Basic rate, relief at source: each £100 of take-home puts £125 in; Nest's charges apply.
    expected = project(series, [PER] * months, nest_fees(), extra=lambda i, own: own * 0.25)[-1]
    assert pension.added == pytest.approx(25 * months) and pension.pot == pytest.approx(expected.value)
    assert pension.spendable == pytest.approx(expected.value * (0.25 + 0.75 * 0.8))
    assert isa.at_60 is None and pension.at_60 is None


def test_lisa_is_a_row_only_when_turned_on_with_its_bonus_until_50_and_charge_before_60(ctx):
    use(ctx, replace(BASIC, lisa=True))
    c = wrapper_comparison(ctx)
    lisa = rows(c)["lisa"]
    assert c.lisa is True and list(rows(c)) == ["isa", "pension", "lisa"]
    assert lisa.paid_in == pytest.approx(PER * 20 * 12) and lisa.added == pytest.approx(25 * 20 * 12)
    assert lisa.spendable == pytest.approx(lisa.pot * 0.75) and lisa.at_60 > lisa.pot
    assert all(r.at_60 is not None and r.paid_in == lisa.paid_in for r in c.rows)  # same window, fair per £1


@pytest.mark.parametrize("born", [1980, 2010])
def test_lisa_on_outside_18_to_39_says_it_cannot_be_opened(ctx, born):
    use(ctx, replace(BASIC, lisa=True, born=born))
    c = wrapper_comparison(ctx)
    assert "lisa" not in rows(c) and any("before 40" in n for n in c.notes)


def best(c, keys, at_60):
    def per_pound(r):
        return (r.at_60 if at_60 else r.spendable) / r.paid_in

    assert all(rows(c)[k].per_pound == pytest.approx(per_pound(rows(c)[k])) for k in keys)
    return max(keys, key=lambda k: per_pound(rows(c)[k]))


def test_the_order_is_employer_pension_then_isa_for_money_needed_early_then_the_best_for_the_rest(ctx):
    use(ctx, replace(BASIC, salary=60_000))
    c = wrapper_comparison(ctx)
    assert [s.wrapper for s in c.steps] == ["pension", "isa", "pension"]
    assert c.steps[1].monthly == 200 and c.steps[2].monthly == 300
    assert c.steps[2].into == pytest.approx(300 / 0.6)  # higher rate: £300 of take-home is £500 gross
    assert "employer" in c.steps[0].text


def test_with_lisa_on_a_basic_rate_taxpayer_fills_the_lisa_first_then_the_next_best(ctx):
    use(ctx, replace(BASIC, lisa=True), monthly=600)
    c = wrapper_comparison(ctx)
    assert best(c, ["isa", "pension", "lisa"], at_60=True) == "lisa"
    rest = [s for s in c.steps[2:]]
    assert rest[0].wrapper == "lisa" and rest[0].monthly == pytest.approx(4_000 / 12)
    assert rest[0].into == pytest.approx(4_000 / 12 * 1.25)
    assert rest[1].wrapper == best(c, ["isa", "pension"], at_60=True)
    assert rest[1].monthly == pytest.approx(400 - 4_000 / 12)


def test_isa_over_the_allowance_is_said(ctx):
    use(ctx, replace(BASIC, needed_before=2_000), monthly=2_000)
    c = wrapper_comparison(ctx)
    assert c.isa_yearly == 24_000 and c.isa_allowance == 20_000
    assert any("allowance" in n and "£4,000" in n for n in c.notes)


def test_no_employer_money_and_below_the_minimum_are_said(ctx):
    use(ctx, replace(BASIC, employer_pct=0, employee_pct=0))
    c = wrapper_comparison(ctx)
    assert c.steps[0].wrapper == "pension" and "No employer money" in c.steps[0].text
    assert any("auto-enrolment minimum" in n for n in c.notes)


def test_rates_are_dated_with_sources(ctx):
    c = wrapper_comparison(ctx)
    assert c.tax_year == "2026-27" and c.checked == date(2026, 10, 3)
    assert all(url.startswith("https://") for _, url in c.sources)


def test_save_writes_the_lisa_switch_and_numbers_and_refuses_bad_ones(ctx):
    save_wrappers(ctx, {"lisa": True, "salary": 30_000})
    assert ctx.config.wrappers.lisa is True and ctx.config.wrappers.salary == 30_000
    saved = tomllib.loads(ctx.config_source.read_text(encoding="utf-8"))["wrappers"]
    assert saved == {"lisa": True, "salary": 30_000}
    with pytest.raises(InvalidQuery, match="employee_pct"):
        save_wrappers(ctx, {"employee_pct": 150})
    with pytest.raises(InvalidQuery, match="unknown"):
        save_wrappers(ctx, {"pot": 1})
    assert ctx.config.wrappers.employee_pct == 0


def test_growth_out_of_range_is_refused(ctx):
    use(ctx)
    with pytest.raises(InvalidQuery, match="growth"):
        wrapper_comparison(ctx, growth=0.9)
