"""The 7-day cool-off: a change to the real-money plan waits as pending, can be cancelled, and takes
effect on its own once 7 days have passed. A first entry into a blank part applies at once."""

from datetime import datetime, timedelta, timezone

import pytest

from oracle.config import Config, PlanConfig
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.guardrails_service import (
    COOL_OFF, cancel_pending, change_plan, guardrails, settle,
)
from oracle.services.settings_service import set_plan, set_setting
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
SET = PlanConfig(percent_fee=0.25, flat_fee=4.99, fund="all_world", monthly=500.0, rule="Never sell in a fall.")


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def ctx(tmp_path, clock):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None, clock) as c:
        c.config_source = tmp_path / "oracle.toml"
        yield c


@pytest.fixture
def started(ctx):
    set_plan(ctx, {k: getattr(SET, k) for k in ("percent_fee", "flat_fee", "fund", "monthly", "rule")})
    return ctx


def test_filling_blank_parts_applies_at_once(ctx):
    got = change_plan(ctx, {"monthly": 300, "fund": "sp500", "percent_fee": 0.25, "rule": "Hold."})
    assert set(got.applied) == {"monthly", "fund", "percent_fee", "rule"} and got.pending is None
    assert ctx.config.plan.monthly == 300 and ctx.config.plan.fund == "sp500" and ctx.config.plan.rule == "Hold."


def test_an_unchanged_value_is_neither_saved_nor_pending(ctx):
    got = change_plan(ctx, {"fund": "default", "monthly": 0})
    assert got.applied == () and got.pending is None


def test_a_change_to_a_set_value_waits_seven_days_then_takes_effect(started, clock):
    got = change_plan(started, {"monthly": 50})
    assert got.applied == () and got.pending.changes == {"monthly": (500.0, 50)}
    assert got.pending.due == NOW + COOL_OFF
    assert started.config.plan.monthly == 500.0
    clock.advance((COOL_OFF - timedelta(seconds=1)).total_seconds())
    assert settle(started) is None and started.config.plan.monthly == 500.0
    clock.advance(1)
    done = settle(started)
    assert done.state == "applied" and started.config.plan.monthly == 50
    assert guardrails(started).pending is None
    assert guardrails(started).history[0].state == "applied"
    assert "monthly = 50" in (started.config_source.read_text(encoding="utf-8"))


def test_a_cancelled_change_never_takes_effect(started, clock):
    change_plan(started, {"fund": "sp500"})
    gone = cancel_pending(started)
    assert gone.state == "cancelled" and gone.changes == {"fund": ("all_world", "sp500")}
    clock.advance((COOL_OFF * 2).total_seconds())
    assert settle(started) is None and started.config.plan.fund == "all_world"
    with pytest.raises(InvalidQuery, match="nothing pending"):
        cancel_pending(started)


def test_the_platform_keys_are_one_part_so_switching_type_on_a_set_platform_waits(started):
    got = change_plan(started, {"platform": "flat", "percent_fee": 0.25, "flat_fee": 4.99})
    assert got.applied == () and got.pending.changes == {"platform": ("percentage", "flat")}


def test_another_change_joins_the_pending_one_and_restarts_the_clock(started, clock):
    change_plan(started, {"monthly": 50})
    clock.advance(timedelta(days=3).total_seconds())
    got = change_plan(started, {"fund": "developed"})
    assert got.pending.changes == {"monthly": (500.0, 50), "fund": ("all_world", "developed")}
    assert got.pending.due == clock.utcnow() + COOL_OFF


def test_setting_a_pending_key_back_to_its_value_drops_it_and_an_empty_change_is_cancelled(started, clock):
    change_plan(started, {"monthly": 50, "fund": "sp500"})
    due = guardrails(started).pending.due
    clock.advance(60)
    got = change_plan(started, {"monthly": 500})
    assert got.pending.changes == {"fund": ("all_world", "sp500")} and got.pending.due == due, "an undo keeps the clock"
    got = change_plan(started, {"fund": "all_world"})
    assert got.pending is None and guardrails(started).history[0].state == "cancelled"


def test_a_bad_value_is_refused_now_not_in_seven_days(started):
    with pytest.raises(InvalidQuery, match="monthly"):
        change_plan(started, {"monthly": -5})
    with pytest.raises(InvalidQuery, match="gold"):
        change_plan(started, {"fund": "gold"})
    assert guardrails(started).pending is None


def test_clearing_the_rule_is_a_change_too(started):
    got = change_plan(started, {"rule": "  "})
    assert got.pending.changes == {"rule": ("Never sell in a fall.", "")}


def test_a_too_long_rule_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="rule"):
        change_plan(ctx, {"rule": "x" * 1001})


def test_the_settings_form_cannot_skip_the_cool_off(started):
    with pytest.raises(InvalidQuery, match="7 days"):
        set_setting(started, "plan", "monthly", 10)
    assert started.config.plan.monthly == 500.0
