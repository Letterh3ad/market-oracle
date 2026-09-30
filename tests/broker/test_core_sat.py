from dataclasses import replace
from datetime import date

import pytest

from oracle.broker.core_sat import (CORE_SHARE, EXPIRED, REBALANCE, core_replay, core_sat_replay, rebalance_buys,
                                    rebalance_days, rebalance_sells)
from oracle.broker.orders import Side
from oracle.broker.rules import Lean
from tests.analysts.bars import weekdays
from tests.broker.test_backtest import RISK, RULES, priced, rising

CAPITAL = 100_000.0


def market(n=700):
    return {"UP": priced(rising(n, "UP", 0.002)), "FLAT": priced(rising(n, "FLAT", 0.0)),
            "DOWN": priced(rising(n, "DOWN", -0.001))}


def core(history, start, end=None, members=None, **kw):
    end = end or max(p.bar.date for rows in history.values() for p in rows)
    return core_replay(history, start, end, capital=CAPITAL, members=members or list(history), slippage=0.001,
                       commission=0.0, **kw)


def test_rebalance_days_are_the_first_market_day_of_each_year_from_the_first():
    days = weekdays(600, date(2024, 1, 3))
    assert rebalance_days(days) == [date(2024, 1, 3), date(2025, 1, 3), date(2026, 1, 5)]


def test_sells_trim_overweights_and_drop_non_members_then_buys_fill_underweights_within_cash():
    held = {"A": (100, 60_000.0), "B": (10, 20_000.0), "GONE": (5, 5_000.0)}
    sells = rebalance_sells(["A", "B", "C"], held, cash=15_000.0)
    # Equity 100k over 3 members: 33.3k each; A trims 26.7k at 600 a share, GONE goes.
    assert {(o.symbol, o.shares) for o in sells} == {("A", 44), ("GONE", 5)}
    assert all(o.side == Side.SELL and REBALANCE in o.reason for o in sells)
    buys = rebalance_buys(["A", "B", "C"], {"A": (56, 33_600.0), "B": (10, 20_000.0)}, cash=46_000.0)
    wanted = {"B": 33_200.0 - 20_000.0, "C": 33_200.0}
    assert {o.symbol for o in buys} == {"B", "C"}
    assert sum(o.cash for o in buys) == pytest.approx(46_000.0)
    by = {o.symbol: o.cash for o in buys}
    assert by["B"] / by["C"] == pytest.approx(wanted["B"] / wanted["C"])


def test_buys_never_spend_more_than_the_cash_given():
    buys = rebalance_buys(["A", "B"], {}, cash=1_000.0)
    assert sum(o.cash for o in buys) == pytest.approx(1_000.0)
    assert rebalance_buys(["A"], {}, cash=0.0) == []


def test_core_buys_equal_amounts_at_the_next_open_with_costs_and_holds():
    h = market()
    start = h["UP"][300].bar.date
    r = core(h, start)
    first = [t for t in r.trades if t.day == r.trades[0].day]
    assert {t.symbol for t in first} == {"UP", "FLAT", "DOWN"} and first[0].day > start
    assert all(t.costs > 0 for t in r.trades)
    spent = [-t.cash for t in first]
    assert max(spent) - min(spent) < 0.01 * CAPITAL
    assert sum(spent) <= CAPITAL


def test_core_rebalances_yearly_back_to_equal_weight():
    h = market()
    start = h["UP"][100].bar.date
    r = core(h, start)
    sold = [t for t in r.trades if t.side == Side.SELL]
    assert sold and {t.symbol for t in sold} == {"UP"}
    yearly = rebalance_days([p.bar.date for p in h["UP"] if p.bar.date >= start])
    assert len(yearly) == 3
    assert {d for d, o in r.orders if o.action.value == "sell"} == set(yearly[1:])
    bought_after = {d for d, o in r.orders if o.action.value == "buy"} - {yearly[0]}
    assert bought_after and all(any(y < d for y in yearly[1:]) for d in bought_after)
    # After each sale the laggards are topped up from its proceeds, never beyond the cash.
    cash = CAPITAL
    for t in sorted(r.trades, key=lambda t: (t.day, t.side != Side.SELL)):
        cash += t.cash
        assert cash >= -1e-6


def test_a_stale_holding_is_left_alone_an_unfilled_order_expires_and_a_sub_share_top_up_is_never_placed():
    h = market()
    start = h["UP"][100].bar.date
    h["GAP"] = priced(rising(300, "GAP", 0.001))  # stops long before the first yearly rebalance
    h["LATE"] = priced(rising(101, "LATE", 0.001))  # its last bar is the first day: its buy never fills
    h["BIG"] = [replace(p, bar=replace(p.bar, open=p.bar.open * 1e6, close=p.bar.close * 1e6))
                for p in priced(rising(700, "BIG", 0.001))]
    r = core(h, start)
    assert "GAP" in {t.symbol for t in r.trades} and "GAP" not in {t.symbol for t in r.trades if t.side == Side.SELL}
    assert any(f"LATE: {EXPIRED}" in n for n in r.rejected)
    assert "LATE" not in r.unfilled
    assert {d for d, o in r.orders if o.action.value == "sell"}, "the expired order never blocked a rebalance"
    assert "BIG" not in {o.symbol for _, o in r.orders} and not any("whole share" in n for n in r.rejected)


def test_a_listing_left_out_of_the_members_is_never_bought():
    h = market()
    r = core(h, h["UP"][300].bar.date, members=["UP", "FLAT"])
    assert "DOWN" not in {t.symbol for t in r.trades}


def test_the_satellite_never_spends_core_cash():
    h = market()
    start = h["UP"][300].bar.date

    def always_buy(symbol, bars):
        return Lean(symbol, bars[-1].date, "strong buy", 0.99, "regime")

    risk = {**RISK, "account": CAPITAL}
    x = core_sat_replay(h, start, h["UP"][-1].bar.date, rules=RULES, risk=risk, members=list(h), slippage=0.001,
                        commission=0.0, lean=always_buy)
    sat_cash = (1 - CORE_SHARE) * CAPITAL
    for t in sorted(x.satellite.trades, key=lambda t: (t.day, t.side != Side.SELL)):
        sat_cash += t.cash
        assert sat_cash >= -1e-6
    assert sum(-t.cash for t in x.satellite.trades if t.side == Side.BUY) <= (1 - CORE_SHARE) * CAPITAL + 1e-6
    core_spent = sum(-t.cash for t in x.core.trades if t.side == Side.BUY and t.day == x.core.trades[0].day)
    assert core_spent == pytest.approx(CORE_SHARE * CAPITAL, rel=0.01)
    days = [d for d, _ in x.equity]
    assert days == [d for d, _ in x.core.equity] == [d for d, _ in x.satellite.equity]
    assert x.equity[0][1] == pytest.approx(CAPITAL)
    assert all(v == pytest.approx(a + b) for (_, v), (_, a), (_, b) in zip(x.equity, x.core.equity,
                                                                          x.satellite.equity))


def test_a_future_bar_never_changes_a_past_core_sat_decision():
    h = market()
    start = h["UP"][300].bar.date
    cut = h["UP"][560].bar.date
    changed = {s: rows[:561] + [replace(p, bar=replace(p.bar, open=p.bar.open * 0.5, close=p.bar.close * 0.5,
                                                         adj_close=p.bar.close * 0.5)) for p in rows[561:]]
               for s, rows in h.items()}

    def trend(symbol, bars):
        return Lean(symbol, bars[-1].date, "buy" if bars[-1].close > bars[-20].close else "sell", 0.9, "regime")

    risk = {**RISK, "account": CAPITAL}

    def run(history):
        return core_sat_replay(history, start, h["UP"][-1].bar.date, rules=RULES, risk=risk, members=list(history),
                               slippage=0.001, commission=0.0, lean=trend)

    a, b = run(h), run(changed)

    def upto(x):
        return ([e for e in x.equity if e[0] <= cut], [t for t in x.trades if t.day <= cut])

    assert upto(a) == upto(b)
    assert a.equity[-1] != b.equity[-1]
