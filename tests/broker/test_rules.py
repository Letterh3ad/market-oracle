from datetime import date

import pytest

from oracle.broker.rules import Lean, Position, Rules, decide

DAY = date(2026, 9, 22)
RULES = Rules(min_probability=0.6, max_position=0.10, max_positions=10, cash_floor=0.20, stop_loss=0.08)


def lean(symbol, label="buy", probability=0.7):
    return Lean(symbol, DAY, label, probability, "baseline")


def run(leans=(), holdings=(), cash=10_000.0, sizes=None, pending=()):
    held = {h.symbol: h for h in holdings}
    equity = cash + sum(h.value or 0 for h in holdings)
    sizes = sizes if sizes is not None else {x.symbol: 0.10 for x in leans}
    return {d.symbol: d for d in decide({x.symbol: x for x in leans}, held, cash, equity, sizes, RULES, set(pending))}


def test_buys_on_a_confident_buy_lean_sized_by_risk():
    d = run([lean("A")], sizes={"A": 0.05})
    assert (d["A"].action, d["A"].cash) == ("buy", pytest.approx(500.0))


def test_the_position_cap_limits_a_large_risk_size():
    assert run([lean("A")], sizes={"A": 0.5})["A"].cash == pytest.approx(1000.0)


def test_below_the_probability_threshold_or_not_a_buy_label_is_skipped():
    d = run([lean("A", probability=0.55), lean("B", "hold", 0.9)])
    assert d["A"].action == "skip" and "probability" in d["A"].reason
    assert "B" not in d or d["B"].action == "skip"


def test_ten_positions_blocks_a_new_buy_and_says_why():
    holdings = [Position(f"H{i}", 1, 100.0, 100.0) for i in range(10)]
    d = run([lean("A")], holdings, cash=9000.0)
    assert d["A"].action == "skip" and "10 positions" in d["A"].reason


def test_the_cash_floor_is_never_spent():
    # Equity 10,000: floor 2,000; with 2,300 cash only 300 is spendable.
    holdings = [Position("H", 1, 7700.0, 7700.0)]
    assert run([lean("A")], holdings, cash=2300.0)["A"].cash == pytest.approx(300.0)
    d = run([lean("B")], [Position("H", 1, 8060.0, 8060.0)], cash=1940.0)
    assert d["B"].action == "skip" and "cash floor" in d["B"].reason


def test_a_sell_lean_sells_the_whole_position():
    d = run([lean("H", "sell")], [Position("H", 5, 500.0, 520.0)])
    assert d["H"].action == "sell" and "sell" in d["H"].reason


def test_a_stop_sells_even_while_the_lean_is_still_buy():
    d = run([lean("H", "buy")], [Position("H", 5, 1000.0, 919.0)])
    assert d["H"].action == "sell" and "8%" in d["H"].reason
    assert run([lean("H", "buy")], [Position("H", 5, 1000.0, 921.0)]).get("H") is None


def test_a_held_or_pending_listing_is_not_bought_again():
    assert "H" not in run([lean("H")], [Position("H", 5, 500.0, 500.0)])
    d = run([lean("A"), lean("H", "sell")], [Position("H", 5, 500.0, 500.0)], pending=["A", "H"])
    assert [(d[s].action, d[s].reason) for s in "AH"] == [("skip", "an order is already pending")] * 2


def test_no_risk_size_means_no_buy():
    for sizes in ({}, {"A": 0.0}):
        d = run([lean("A")], sizes=sizes)
        assert d["A"].action == "skip" and "risk" in d["A"].reason


def test_a_pending_buy_takes_a_position_slot():
    holdings = [Position(f"H{i}", 1, 100.0, 100.0) for i in range(9)]
    d = run([lean("A")], holdings, cash=9000.0, pending=["P"])
    assert d["A"].action == "skip" and "10 positions" in d["A"].reason
