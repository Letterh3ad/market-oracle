"""The strategy backtest engine: target weights decided at a close fill at the next open, after UK
retail costs; the platform fee comes off at each month end; the trend overlay comes out the same
as its own simulator."""

import random
from datetime import date, timedelta

import pytest

from oracle.broker.projection import FeeModel
from oracle.broker.strategy_backtest import simulate
from oracle.broker.trend_overlay import Session, month_end_decisions
from oracle.broker.trend_overlay import simulate as overlay
from oracle.broker.uk_costs import Listing, UkCosts

FREE = UkCosts(FeeModel(), half_spreads={}, default_half_spread=0.0, stamp_duty=0.0)
UK = Listing("A.L", "GBP", True, "Large Cap")
US = Listing("B", "USD", False, "Large Cap")


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def series(days, closes, opens=None):
    return {d: Session(d, (opens or closes)[i], c) for i, (d, c) in enumerate(zip(days, closes))}


DAYS = weekdays(date(2020, 1, 1), 5)


def test_weights_decided_at_a_close_fill_at_the_next_open_and_grow_with_the_close():
    prices = {"A.L": series(DAYS, [100, 110, 121, 121, 133.1], opens=[100, 100, 121, 121, 121])}
    run = simulate(DAYS, prices, {"A.L": UK}, lambda d: {"A.L": 1.0} if d == DAYS[0] else None, DAYS[0], DAYS[-1],
                   FREE, 1000.0)
    assert [v for _, v in run.equity] == pytest.approx([1000, 1100, 1210, 1210, 1331])
    assert len(run.trades) == 1 and run.trades[0].day == DAYS[1] and run.trades[0].value == pytest.approx(1000)


def test_a_buy_pays_its_costs_out_of_the_money_committed():
    prices = {"A.L": series(DAYS, [100.0] * 5)}
    costs = UkCosts(FeeModel(dealing=5.0, fx=0.01))
    run = simulate(DAYS, prices, {"A.L": UK}, lambda d: {"A.L": 1.0}, DAYS[0], DAYS[-1], costs, 10_000.0)
    t = run.trades[0]
    value = (10_000 - 5) / (1 + 0.001 + 0.005)
    assert t.value == pytest.approx(value)
    assert (t.cost.dealing, t.cost.spread, t.cost.stamp, t.cost.fx) == pytest.approx((5, value * 0.001, value * 0.005,
                                                                                       0))
    assert run.equity[-1][1] == pytest.approx(value) and len(run.trades) == 1, "no churn: the target holds"
    assert run.costs.total == pytest.approx(10_000 - value)


def test_a_rebalance_sells_first_then_buys_and_counts_turnover():
    prices = {"A.L": series(DAYS, [100, 200, 200, 200, 200], opens=[100, 100, 200, 200, 200]), "B": series(DAYS, [100.0] * 5)}
    half = {"A.L": 0.5, "B": 0.5}
    run = simulate(DAYS, prices, {"A.L": UK, "B": US}, lambda d: half if d in (DAYS[0], DAYS[1]) else None,
                   DAYS[0], DAYS[-1], FREE, 1000.0)
    sides = [(t.day, t.symbol, t.side) for t in run.trades]
    assert sides[:2] == [(DAYS[1], "A.L", "buy"), (DAYS[1], "B", "buy")]
    assert sides[2:] == [(DAYS[2], "A.L", "sell"), (DAYS[2], "B", "buy")]
    assert run.equity[-1][1] == pytest.approx(1500)
    assert run.traded == pytest.approx(500 + 500 + 250 + 250)


def test_the_platform_fee_comes_off_each_month_end_close():
    days = weekdays(date(2020, 1, 27), 20)
    prices = {"A.L": series(days, [100.0] * 20)}
    costs = UkCosts(FeeModel(percent=0.012), half_spreads={}, default_half_spread=0.0)
    run = simulate(days, prices, {"A.L": UK}, lambda d: None, days[0], days[-1], costs, 1000.0)
    ends = [d for d, n in zip(days, days[1:]) if d.month != n.month]
    assert len(ends) == 1 and run.platform == pytest.approx(1.0)
    assert dict(run.equity)[ends[0]] == pytest.approx(999.0) and run.equity[-1][1] == pytest.approx(999.0)


def test_a_flat_fee_is_charged_in_pounds_whatever_the_pot():
    days = weekdays(date(2020, 1, 27), 20)
    costs = UkCosts(FeeModel(flat=10.0), half_spreads={}, default_half_spread=0.0)
    run = simulate(days, {"A.L": series(days, [100.0] * 20)}, {"A.L": UK}, lambda d: None, days[0], days[-1], costs,
                   20_000.0)
    assert run.platform == pytest.approx(10.0) and run.equity[-1][1] == pytest.approx(19_990.0)


def test_the_last_close_decides_nothing_and_no_day_after_it_is_read():
    seen = []
    prices = {"A.L": series(DAYS, [100.0] * 5)}
    simulate(DAYS, prices, {"A.L": UK}, lambda d: seen.append(d), DAYS[1], DAYS[3], FREE, 1.0)
    assert seen == DAYS[1:3]


def test_the_trend_overlay_through_the_engine_matches_its_own_simulator():
    rng = random.Random(7)
    days = weekdays(date(1990, 1, 1), 3000)
    closes, opens, c = [], [], 100.0
    for _ in days:
        o = c * (1 + rng.gauss(0, 0.004))
        c = o * (1 + rng.gauss(0.0003, 0.01))
        opens.append(o)
        closes.append(c)
    sessions = [Session(d, o, x) for d, o, x in zip(days, opens, closes)]
    start, end = days[400], days[-1]
    want = overlay(sessions, start, end, 0.001)
    calls = dict(month_end_decisions(sessions))
    first_call = next((calls[s.day] for s in reversed(sessions[:400]) if s.day in calls), None)
    run = simulate(days, {"X": {s.day: s for s in sessions}}, {"X": Listing("X", "USD", False)},
                   lambda d: None if calls.get(d) is None else {"X": 1.0 if calls[d] else 0.0}, start, end,
                   UkCosts(FeeModel()), 1.0, initial={"X": 0.0 if first_call is False else 1.0})
    assert want.switches > 20 and len(run.trades) == want.switches
    assert [v for _, v in run.equity] == pytest.approx([v for _, v in want.equity], rel=1e-4)
