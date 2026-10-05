"""The strategy test: a registered rule against index buy-and-hold after UK retail costs, judged on
confirm by excess CAGR and the deflated Sharpe of its daily excess given the variants tried; kept
beside the signal tests and shown on the scoreboard."""

import math
import random
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config, PlanConfig
from oracle.market.bars import Bar
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, NoPrices, NotRegistered
from oracle.services.lab_registry import Template, register_test
from oracle.services.lab_service import scoreboard
from oracle.services.signal_test_service import signal_tests
from oracle.services.strategy_service import (
    BENCHMARK, MATCH, POT, Strategy, cost_view, price_trade, run_and_keep_strategy, run_strategy, strategy_tests, trend_check,
)
from oracle.services.trend_service import run_and_keep_trend, trend_tests
from oracle.services.testbed_service import research_context
from oracle.store.bars import BarRepo
from oracle.store.lab import LabTestRepo
from tests.fakes.clock import FakeClock
from tests.services.test_trend_overlay_service import env  # noqa: F401

NOW = datetime(2022, 1, 3, 12, 0, tzinfo=timezone.utc)


def weekdays(a: date, b: date) -> list[date]:
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


DAYS = weekdays(date(2009, 6, 1), date(2021, 12, 31))


def walks(seed: int) -> dict[str, list[float]]:
    """The index; AAA, the index plus 0.08% a day and a little noise; BBB, the index plus 0.01%."""
    rng, out = random.Random(seed), {BENCHMARK: [100.0], "AAA": [100.0], "BBB": [100.0]}
    for _ in DAYS[1:]:
        r = rng.gauss(0.0003, 0.01)
        for sym, extra, noise in ((BENCHMARK, 0, 0), ("AAA", 0.0008, 0.005), ("BBB", 0.0001, 0.0)):
            out[sym].append(out[sym][-1] * (1 + r + extra + rng.gauss(0, noise) * bool(noise)))
    return out


def seed(ctx, closes):
    with research_context(ctx) as r:
        BarRepo(r.conn).upsert([Bar(sym, d, c, c, c, c, c, 0, "USD", "yahoo") for sym, cs in closes.items()
                                for d, c in zip(DAYS, cs)], "2022-01-01T00:00:00+00:00")


def registered_strategy(ctx, key, variants=1, commit=True):
    register_test(ctx, Template(key, f"{key} rule", "strategy", (), "the rule", "two stocks", "positive", "beats it",
                                "a book", "n/a", "UK retail", "confirm", variants=variants), today="2022-01-01")
    if commit:
        with research_context(ctx) as r:
            LabTestRepo(r.conn).set_commit(key, "b" * 40)


def hold(symbol):
    return lambda prices: (lambda d: {symbol: 1.0})


def churn(prices):
    """Swaps AAA-free BBB for cash every day: all turnover."""
    return lambda d: {"BBB": float(d.toordinal() % 2)}


REGISTRY = {"hold_aaa": Strategy("hold_aaa", "Hold AAA", ("AAA",), hold("AAA")),
            "churn_bbb": Strategy("churn_bbb", "Churn BBB", ("BBB",), churn)}


@pytest.fixture
def ctx(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db",
                    plan=PlanConfig(dealing_fee=5.0, fx_fee=0.5, percent_fee=0.25))
    with open_context(config, None, FakeClock(NOW)) as c:
        yield c


def test_a_strategy_that_beats_the_index_after_costs_with_a_strong_dsr_passes(ctx):
    seed(ctx, walks(1))
    registered_strategy(ctx, "hold_aaa")
    t = run_strategy(ctx, "hold_aaa", REGISTRY)
    c = t.confirm
    assert t.verdict == "PASS" and t.passed and c.verdict == "PASS" and t.train.verdict is None
    assert c.start == date(2020, 1, 1) and c.end == date(2021, 12, 31) and t.train.end == date(2019, 12, 31)
    assert c.excess > 0 and c.dsr >= 0.95 and t.variants == 1 and t.pot == POT
    s = c.strategy
    assert s.trades == 1 and s.dealing == 5.0 and s.stamp == 0, "one buy of a US share: dealing, no stamp duty"
    assert s.fx == pytest.approx((POT - 5) / (1 + 0.001 + 0.005) * 0.005), "FX on a non-GBP listing"
    assert c.index.fx == 0 and c.index.stamp == 0 and c.index.trades == 1, "the index is a GBP fund"
    assert s.platform > 0 and s.platform == pytest.approx(c.index.platform, rel=0.2), "both pay the platform fee"
    assert c.gross.cagr > s.cagr and c.index_gross.cagr > c.index.cagr


def test_the_same_edge_fails_when_many_variants_were_tried(ctx):
    seed(ctx, walks(1))
    registered_strategy(ctx, "hold_aaa", variants=100_000)
    t = run_strategy(ctx, "hold_aaa", REGISTRY)
    assert t.confirm.excess > 0 and t.confirm.dsr < 0.95 and t.verdict == "FAIL"
    assert "100000 variants" in t.bar_rule and "100000 variants tried" in " ".join(t.notes)


def test_a_rule_that_works_only_before_costs_fails_and_says_so(ctx):
    seed(ctx, walks(2))
    registered_strategy(ctx, "churn_bbb")
    t = run_strategy(ctx, "churn_bbb", REGISTRY)
    assert t.verdict == "FAIL" and t.confirm.excess < 0 < t.confirm.strategy.turnover
    assert t.confirm.strategy.trades > 400 and t.confirm.strategy.dealing == 5.0 * t.confirm.strategy.trades


def test_a_gross_only_edge_fails_and_names_itself(ctx):
    """Swapping AAA and BBB each week holds a beating stock all the time, but each swap pays two
    dealing fees, two half spreads and FX both ways."""
    seed(ctx, walks(2))
    registered_strategy(ctx, "swap")
    swap = {"swap": Strategy("swap", "Weekly swap", ("AAA", "BBB"),
                             lambda p: (lambda d: {"AAA" if d.isocalendar()[1] % 2 else "BBB": 1.0}))}
    t = run_strategy(ctx, "swap", swap)
    assert t.confirm.gross_excess > 0 > t.confirm.excess and t.verdict == "FAIL"
    assert any("Works only before costs" in n for n in t.notes)
    assert t.confirm.strategy.turnover > 20, "a whole pot each week"


def test_a_run_needs_code_and_a_committed_strategy_registration(ctx):
    seed(ctx, walks(1))
    with pytest.raises(InvalidQuery, match="no strategy code"):
        run_strategy(ctx, "nope", REGISTRY)
    with pytest.raises(NotRegistered, match="not pre-registered"):
        run_strategy(ctx, "hold_aaa", REGISTRY)
    registered_strategy(ctx, "hold_aaa", commit=False)
    with pytest.raises(NotRegistered, match="not committed"):
        run_strategy(ctx, "hold_aaa", REGISTRY)
    with pytest.raises(InvalidQuery, match="not a strategy"):
        run_strategy(ctx, "momentum_12_1", {"momentum_12_1": REGISTRY["hold_aaa"]})


def test_a_kept_result_loads_back_shows_on_the_scoreboard_and_is_no_signal_test(ctx):
    seed(ctx, walks(1))
    registered_strategy(ctx, "hold_aaa")
    kept = run_and_keep_strategy(ctx, "hold_aaa", REGISTRY)
    assert strategy_tests(ctx) == {"hold_aaa": kept} and kept.id is not None
    assert "hold_aaa" not in signal_tests(ctx)
    row = next(r for r in scoreboard(ctx).rows if r.key == "hold_aaa")
    assert row.verdict == "PASS" and row.value == pytest.approx(kept.confirm.excess)
    assert row.measure == "CAGR over the index after costs" and row.dsr == pytest.approx(kept.confirm.dsr)
    assert row.train == pytest.approx(kept.train.excess) and row.turnover == pytest.approx(kept.confirm.strategy.turnover)
    assert row.break_even is None


async def test_the_trend_overlay_reproduces_through_the_engine_and_is_never_judged_again(env):
    ctx, _ = env
    with pytest.raises(NoPrices, match="nothing to reproduce"):
        trend_check(ctx)
    kept = await run_and_keep_trend(ctx)
    before = scoreboard(ctx)
    row = next(r for r in before.rows if r.key == "trend_overlay")
    assert row.train == pytest.approx(kept.choose.trend.cagr - kept.choose.held.cagr)
    check = trend_check(ctx)
    assert check.matches and check.recorded_verdict == kept.verdict and check.last_day == kept.last_day
    assert {(r.window, r.measure) for r in check.rows} == {(w, m) for w in ("choose", "confirm")
                                                           for m in ("return", "CAGR", "worst fall", "switches")}
    confirm = {r.measure: r for r in check.rows if r.window == "confirm"}
    assert confirm["switches"].recorded == kept.confirm.switches > 0
    assert MATCH <= 1e-9, "within float rounding"
    for r in check.rows:
        assert abs(r.reproduced - r.recorded) <= MATCH, r
    assert trend_tests(ctx) == kept and scoreboard(ctx) == before, "nothing kept, nothing re-judged"


async def test_a_reproduction_off_by_more_than_rounding_says_so(env, monkeypatch):
    ctx, _ = env
    kept = await run_and_keep_trend(ctx)
    off = replace(kept, confirm=replace(kept.confirm, trend=replace(kept.confirm.trend, cagr=kept.confirm.trend.cagr
                                                                    + 0.001)))
    monkeypatch.setattr("oracle.services.strategy_service.trend_service.trend_tests", lambda c: off)
    check = trend_check(ctx)
    assert not check.matches
    assert [(r.window, r.measure) for r in check.rows if not r.matches] == [("confirm", "CAGR")]


def test_a_london_etf_pays_no_stamp_duty_but_a_london_share_does(ctx):
    w = walks(1)
    seed(ctx, {BENCHMARK: w[BENCHMARK], "VUAG.L": w["AAA"], "BARC.L": w["AAA"]})
    registry = {"etf": Strategy("etf", "Hold VUAG", ("VUAG.L",), hold("VUAG.L"), funds=frozenset({"VUAG.L"})),
                "share": Strategy("share", "Hold BARC", ("BARC.L",), hold("BARC.L"))}
    for key in registry:
        registered_strategy(ctx, key)
    etf, share = (run_strategy(ctx, k, registry).confirm.strategy for k in ("etf", "share"))
    assert etf.stamp == 0 and etf.fx == 0 and etf.trades == 1
    assert share.stamp == pytest.approx((POT - 5) / (1 + 0.001 + 0.005) * 0.005) and share.fx == 0
    assert any("VUAG.L" in n and "fund" in n for n in run_strategy(ctx, "etf", registry).notes)


def test_a_strategy_trading_the_benchmark_pays_what_the_index_pays(ctx):
    seed(ctx, walks(1))
    registered_strategy(ctx, "hold_index")
    t = run_strategy(ctx, "hold_index", {"hold_index": Strategy("hold_index", "Hold it", (BENCHMARK,), hold(BENCHMARK))})
    s, i = t.confirm.strategy, t.confirm.index
    assert s.fx == 0 and s.stamp == 0
    assert (s.ret, s.dealing, s.spread) == pytest.approx((i.ret, i.dealing, i.spread))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1.0])
def test_a_trade_value_must_be_a_finite_number_not_below_zero(ctx, value):
    with pytest.raises(InvalidQuery, match="a number"):
        price_trade(ctx, value)


def test_the_cost_view_reads_the_plan(ctx):
    v = cost_view(ctx)
    assert (v.platform, v.percent_fee, v.dealing_fee, v.fx_fee, v.stamp_duty, v.pot) == (
        "percentage", 0.25, 5.0, 0.5, 0.005, POT)
    assert dict(v.half_spreads)["Small Cap"] == 0.005 and v.default_half_spread == 0.001
    assert any("UK shares only" in n for n in v.notes)
    assert math.isclose(sum(1 for _ in v.half_spreads), 6)
