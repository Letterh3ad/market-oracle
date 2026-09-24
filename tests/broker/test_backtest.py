import random
from dataclasses import dataclass, replace
import pytest

from oracle.analysts.technicals import price_signals
from oracle.broker.backtest import LOOKBACK, Venue, replay
from oracle.broker.fills import COSTS, buy_fill
from oracle.broker.orders import Side
from oracle.broker.rules import Lean, Rules
from oracle.market.bars import Bar
from tests.analysts.bars import series

RULES = Rules(min_probability=0.6, max_position=0.1, max_positions=10, cash_floor=0.2, stop_loss=0.08)
RISK = {"account": 10_000.0, "max_position": 0.1, "cash_floor": 0.2, "target_volatility": 0.2}


@dataclass(frozen=True)
class P:
    bar: Bar
    rate: float | None = 1.0


def rising(n: int, symbol: str = "UP", daily: float = 0.003, wobble: float = 0.004) -> list[Bar]:
    """A steady climb with a daily wobble, so price swings are never zero."""
    closes = [100 * (1 + daily) ** i * (1 + wobble * (-1) ** i) for i in range(n)]
    return [replace(b, open=b.close * 0.995) for b in series(closes, symbol)]


def priced(bars: list[Bar], rate=1.0) -> list[P]:
    return [P(b, rate(b.date) if callable(rate) else rate) for b in bars]


def run(history, start, end=None, rules=RULES, slippage=0.001, commission=0.0):
    end = end or max(p.bar.date for rows in history.values() for p in rows)
    return replay(history, start, end, rules=rules, risk=RISK, slippage=slippage, commission=commission)


def test_a_trending_listing_is_bought_at_the_next_open():
    bars = rising(400)
    start = bars[300].date
    r = run({"UP": priced(bars)}, start)
    first = r.trades[0]
    assert (first.symbol, first.side) == ("UP", Side.BUY)
    [(placed, decision)] = [o for o in r.orders if o[1].symbol == "UP"][:1]
    assert placed >= start and first.day > placed
    fill_bar = next(b for b in bars if b.date == first.day)
    assert first.price == pytest.approx(fill_bar.open * 1.001)
    assert first.shares * first.price <= decision.cash
    assert r.equity[0][0] == start and r.equity[-1][0] == bars[-1].date


def test_slippage_and_commission_are_charged_and_fx_is_the_fill_days_rate():
    bars = rising(400)
    switch = bars[330].date
    rows = priced(bars, lambda d: 1.25 if d < switch else 2.0)
    r = run({"UP": rows}, bars[300].date, slippage=0.002, commission=3.0)
    buy = r.trades[0]
    fill = next(p for p in rows if p.bar.date == buy.day)
    assert buy.price == pytest.approx(fill.bar.open * 1.002)
    assert buy.cash == pytest.approx(-(buy.shares * buy.price / fill.rate + 3.0))
    assert buy.costs == pytest.approx(3.0 + buy.shares * (buy.price - fill.bar.open) / fill.rate)


def test_a_stop_sells_after_a_fall_past_the_limit():
    bars = rising(400)
    crash = bars[360:]
    fallen = bars[:360] + [replace(b, open=b.open * 0.75, close=b.close * 0.75, adj_close=b.close * 0.75)
                           for b in crash]
    r = run({"UP": priced(fallen)}, bars[300].date)
    sells = [t for t in r.trades if t.side == Side.SELL]
    assert sells and sells[0].day > bars[360].date
    assert sells[0].reason.startswith("stop:")
    assert sells[0].shares == r.trades[0].shares


def test_limits_hold_on_positions_size_and_the_cash_floor():
    history = {f"S{i}": priced(rising(400, f"S{i}", daily=0.002 + 0.0005 * i)) for i in range(5)}
    rules = replace(RULES, max_positions=2, max_position=0.5)
    r = run(history, history["S0"][300].bar.date, rules=rules)
    held: dict[str, int] = {}
    most = 0
    for t in r.trades:
        held[t.symbol] = held.get(t.symbol, 0) + (t.shares if t.side == Side.BUY else -t.shares)
        most = max(most, sum(1 for v in held.values() if v))
    assert most == 2
    equity = dict(r.equity)
    for placed, d in r.orders:
        if d.cash is not None:
            assert d.cash <= 0.5 * equity[placed] + 1e-6
    cash = RISK["account"]
    for t in r.trades:
        cash += t.cash
        # The floor is judged against equity at the decision; the fill's open can drift from it.
        assert cash >= 0.2 * equity[max(d for d in equity if d < t.day)] * 0.95


def test_a_future_bar_never_changes_a_past_decision():
    bars = rising(420)
    cut = bars[370].date
    changed = bars[:371] + [replace(b, open=b.open * 0.5, close=b.close * 0.5, adj_close=b.close * 0.5)
                            for b in bars[371:]]
    a = run({"UP": priced(bars)}, bars[300].date)
    b = run({"UP": priced(changed)}, bars[300].date)

    def upto(r):
        return ([e for e in r.equity if e[0] <= cut], [t for t in r.trades if t.day <= cut],
                [o for o in r.orders if o[0] <= cut])

    assert upto(a) == upto(b)
    assert a.equity[-1] != b.equity[-1]


def test_no_lean_without_enough_history_so_nothing_is_bought():
    bars = rising(40)
    r = run({"UP": priced(bars)}, bars[5].date)
    assert r.trades == () and r.equity[-1][1] == RISK["account"]


def test_a_day_without_a_rate_holds_the_order_until_one_comes():
    bars = rising(400)
    gap = {b.date for b in bars[301:306]}
    rows = [P(b, None if b.date in gap else 1.0) for b in bars]
    r = run({"UP": rows}, bars[300].date)
    assert r.trades and r.trades[0].day not in gap
    assert r.trades[0].day >= bars[306].date


def test_the_last_day_places_no_orders():
    bars = rising(400)
    r = run({"UP": priced(bars)}, bars[300].date)
    assert all(day < bars[-1].date for day, _ in r.orders)


def test_the_lookback_leans_as_the_full_history_would():
    rng = random.Random(7)
    closes = [100.0]
    for _ in range(899):
        closes.append(closes[-1] * (1 + rng.gauss(0.0004, 0.015)))
    bars = series(closes)
    for end in (700, 800, 900):
        full = [s for s in price_signals(bars[:end], "TEST") if s.analyst == "technicals"]
        cut = [s for s in price_signals(bars[end - LOOKBACK:end], "TEST") if s.analyst == "technicals"]
        assert (full[0].score, full[0].confidence) == (cut[0].score, cut[0].confidence)


def test_a_lean_source_replaces_the_technicals_and_orders_can_fill_at_the_close():
    bars = rising(400)
    day = bars[320].date
    seen = []

    def leans(symbol, window):
        seen.append(window[-1].date)
        return Lean(symbol, window[-1].date, "buy", 0.9, "test") if window[-1].date >= day else None

    r = replay({"UP": priced(bars)}, bars[300].date, bars[-1].date, rules=RULES, risk=RISK, slippage=0.001,
               commission=0.0, lean=leans, fill_at="close")
    first = r.trades[0]
    assert first.day == bars[321].date and first.reason == "test lean buy at 90%"
    assert first.price == pytest.approx(bars[321].close * 1.001)
    assert max(seen) < bars[-1].date  # the last day decides nothing


def test_venues_charge_the_paper_accounts_full_costs():
    def buy(symbol, window):
        return Lean(symbol, window[-1].date, "buy", 0.9, "test")

    def first_buy(symbol, venue, rate):
        bars = rising(400, symbol)
        r = replay({symbol: priced(bars, rate)}, bars[300].date, bars[-1].date, rules=RULES, risk=RISK, slippage=0.0,
                   commission=0.0, lean=buy, fill_at="close", venues={symbol: venue})
        return r.trades[0], next(b for b in bars if b.date == r.trades[0].day)

    lse, bar = first_buy("VOD.L", Venue("United Kingdom", "LSE"), 1.0)
    fill = buy_fill(-lse.cash + 1e-9, open_price=bar.close, rate=1.0, country="United Kingdom", exchange="LSE",
                    costs=COSTS)
    assert lse.price == pytest.approx(bar.close * 1.001) and lse.shares == fill.shares
    assert lse.costs == pytest.approx(fill.fee + fill.slippage + fill.stamp)
    assert fill.stamp > 0
    usd, bar = first_buy("AAPL", Venue("United States", "NMS"), 1.25)
    fill = buy_fill(-usd.cash + 1e-9, open_price=bar.close, rate=1.25, country="United States", exchange="NMS",
                    costs=COSTS)
    assert usd.costs == pytest.approx(fill.fee / 1.25 + fill.slippage + fill.fx) and fill.fx > 0
