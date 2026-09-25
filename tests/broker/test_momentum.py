from dataclasses import replace
from datetime import date

from oracle.analysts.price_factors import ranked_at
from oracle.broker.backtest import replay
from oracle.broker.momentum import SOURCE, momentum_lean, rank_day
from oracle.broker.orders import Side
from oracle.market.bars import Bar
from tests.broker.test_backtest import RISK, RULES, priced
from tests.analysts.bars import weekdays

N = 520


def climb(symbol: str, rate, wobble: float = 0.004) -> list[Bar]:
    """Weekday bars whose close grows by `rate(i)` a day, with a wobble so swings are never zero."""
    closes, c = [], 100.0
    for i in range(N):
        c *= 1 + rate(i)
        closes.append(c * (1 + wobble * (-1) ** i))
    return [Bar(symbol, d, v * 0.995, v, v, v, v, 1000, "USD", "test") for d, v in zip(weekdays(N), closes)]


def market():
    return {"A": climb("A", lambda i: 0.001), "B": climb("B", lambda i: 0.0 if i < 330 else 0.004),
            "C": climb("C", lambda i: 0.0005)}


def at(history, symbol, day, ranked_from=None):
    """The lean after `day`'s close; the ranks read `ranked_from` (default `history`)."""
    bars = [b for b in history[symbol] if b.date <= day]
    return momentum_lean(ranked_from or history, top=1)(symbol, bars)


def test_rank_day_is_the_last_day_of_the_month_before():
    assert rank_day(date(2026, 3, 1)) == date(2026, 2, 28)
    assert rank_day(date(2026, 3, 31)) == date(2026, 2, 28)
    assert rank_day(date(2026, 1, 15)) == date(2025, 12, 31)


def test_the_top_n_lean_buy_and_the_rest_of_the_ranked_set_sell():
    history = market()
    day = history["A"][300].date
    leans = {s: at(history, s, day) for s in history}
    assert {s: x.label for s, x in leans.items()} == {"A": "buy", "B": "sell", "C": "sell"}
    assert all(x.as_of == rank_day(day) and x.source == SOURCE for x in leans.values())


def test_no_look_ahead_ranks_read_only_bars_up_to_the_rank_day():
    history = market()
    day = history["A"][300].date
    before = {s: at(history, s, day) for s in history}
    # Every bar after the rank day crashes A and rockets C: no lean in the month may notice.
    cut = rank_day(day)
    wild = {s: [b if b.date <= cut else replace(b, close=b.close * (0.01 if s == "A" else 50), adj_close=None)
                for b in bars] for s, bars in history.items()}
    assert {s: at(wild, s, day) for s in history} == before
    known = {t: [b for b in bars if b.date <= cut] for t, bars in history.items()}
    assert {s: at(history, s, day, ranked_from=known) for s in history} == before


def test_a_listing_with_a_break_in_its_year_is_not_ranked():
    history = market()
    day = history["A"][300].date
    history["C"] = [replace(b, close=b.close * 3, adj_close=b.adj_close * 3) if i > 200 else b
                    for i, b in enumerate(history["C"])]
    assert at(history, "C", day) is None
    assert at(history, "A", day).label == "buy"


def test_blending_with_low_volatility_favours_the_calm():
    history = {"WILD": climb("WILD", lambda i: 0.0012, wobble=0.03), "CALM": climb("CALM", lambda i: 0.001)}
    day = history["CALM"][300].date
    only_momentum, blended = momentum_lean(history, 1), momentum_lean(history, 1, low_vol_weight=0.6)
    bars = {s: [b for b in rows if b.date <= day] for s, rows in history.items()}
    assert only_momentum("WILD", bars["WILD"]).label == "buy"
    assert blended("CALM", bars["CALM"]).label == "buy" and blended("WILD", bars["WILD"]).label == "sell"


def test_replayed_it_holds_the_leader_and_rotates_only_after_a_month_end_rank():
    history = market()
    start = history["A"][280].date
    lean = momentum_lean(history, top=1)
    r = replay({s: priced(b) for s, b in history.items()}, start, history["A"][-1].date, rules=RULES, risk=RISK,
               slippage=0.001, commission=0.0, lean=lean)
    buys = [t for t in r.trades if t.side == Side.BUY]
    sells = [t for t in r.trades if t.side == Side.SELL]
    assert [t.symbol for t in buys] == ["A", "B"] and [t.symbol for t in sells] == ["A"]
    # B takes the lead on some month end: A is sold and B bought the month after, never before.
    switch = min(d for d, o in r.orders if o.symbol == "B")
    ranks = ranked_at(history, rank_day(switch))
    assert ranks["B"].momentum_pct > ranks["A"].momentum_pct
    earlier = ranked_at(history, rank_day(rank_day(switch)))
    assert earlier["A"].momentum_pct > earlier["B"].momentum_pct
    assert min(d for d, o in r.orders if o.symbol == "A" and o.action.value == "sell") >= switch
