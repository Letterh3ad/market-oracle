"""The backtest engines credit dividends as cash, by the same rule as the paper ledgers."""

from datetime import timedelta

import pytest

from oracle.broker.dividends import Payout
from tests.broker.test_backtest import P, priced, rising, run
from tests.broker.test_core_sat import core, market


def test_a_traded_line_is_paid_cash_for_its_shares_on_the_pay_date_at_that_days_rate():
    bars = rising(400)
    start = bars[300].date
    plain = run({"UP": priced(bars, 1.25)}, start)
    held = next(t for t in plain.trades if t.side == "buy")
    ex = next(b.date for b in bars if b.date > held.day + timedelta(days=5))
    pay = ex + timedelta(days=7)
    from tests.broker.test_backtest import replay, RISK, RULES
    paid = replay({"UP": priced(bars, 1.25)}, start, bars[-1].date, rules=RULES, risk=RISK, slippage=0.001,
                  commission=0.0, dividends={"UP": [Payout(ex, 2.0, "USD", pay)]})
    assert paid.trades == plain.trades
    gap = dict(paid.equity)[bars[-1].date] - dict(plain.equity)[bars[-1].date]
    assert gap == pytest.approx(held.shares * 2.0 / 1.25)
    before_ex = max(d for d, _ in plain.equity if d < ex)
    assert dict(paid.equity)[before_ex] == pytest.approx(dict(plain.equity)[before_ex])
    for day in (ex, max(d for d, _ in plain.equity if d < pay), pay):
        assert dict(paid.equity)[day] - dict(plain.equity)[day] == pytest.approx(held.shares * 2.0 / 1.25), (
            "owed from the ex-date, counted once on the pay day")
    assert paid.dividends == pytest.approx(held.shares * 2.0 / 1.25)


def test_a_dividend_paid_after_the_window_still_counts_in_its_last_equity():
    bars = rising(400)
    start = bars[300].date
    plain = run({"UP": priced(bars, 1.25)}, start)
    held = next(t for t in plain.trades if t.side == "buy")
    ex = bars[-3].date
    from tests.broker.test_backtest import replay, RISK, RULES
    paid = replay({"UP": priced(bars, 1.25)}, start, bars[-1].date, rules=RULES, risk=RISK, slippage=0.001,
                  commission=0.0, dividends={"UP": [Payout(ex, 2.0, "USD")]})
    shares = sum(t.shares if t.side == "buy" else -t.shares for t in plain.trades if t.day < ex)
    assert held.day < ex and shares > 0
    assert paid.equity[-1][1] - plain.equity[-1][1] == pytest.approx(shares * 2.0 / 1.25)
    assert paid.dividends == 0.0, "not paid inside the window"


def test_a_payout_before_the_first_buy_pays_nothing():
    bars = rising(400)
    start = bars[300].date
    early = Payout(bars[301].date, 5.0, "USD", bars[302].date)
    plain = run({"UP": priced(bars)}, start)
    from tests.broker.test_backtest import replay, RISK, RULES
    paid = replay({"UP": priced(bars)}, start, bars[-1].date, rules=RULES, risk=RISK, slippage=0.001,
                  commission=0.0, dividends={"UP": [early]})
    assert paid.equity == plain.equity and paid.dividends == 0.0


def test_the_core_is_paid_too():
    h = market()
    start = h["UP"][300].bar.date
    plain = core(h, start)
    first = plain.trades[0].day
    ex = h["UP"][320].bar.date
    paid = core(h, start, dividends={"FLAT": [Payout(ex, 1.0, "USD", ex)]})
    shares = sum(t.shares for t in plain.trades if t.symbol == "FLAT" and t.side == "buy" and t.day < ex)
    assert first < ex and shares > 0
    assert paid.equity[-1][1] - plain.equity[-1][1] == pytest.approx(shares * 1.0)


def test_the_core_counts_a_dividend_owed_past_the_window_end():
    h = market()
    start = h["UP"][300].bar.date
    plain = core(h, start)
    ex = h["UP"][-2].bar.date
    paid = core(h, start, dividends={"FLAT": [Payout(ex, 1.0, "USD")]})
    shares = sum(t.shares if t.side == "buy" else -t.shares for t in plain.trades if t.symbol == "FLAT" and t.day < ex)
    assert shares > 0 and paid.dividends == 0.0
    assert paid.equity[-1][1] - plain.equity[-1][1] == pytest.approx(shares * 1.0)
