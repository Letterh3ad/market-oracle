import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from oracle.analysts.price_factors import factors_at, rank, ranked_at
from oracle.market.bars import Bar
from tests.analysts.bars import weekdays

DAY = date(2026, 9, 25)


def daily(closes_by_day, symbol="TEST", adjusted=None):
    return [Bar(symbol, d, c, c, c, c, adjusted(d, c) if adjusted else c, 1000, "USD", "test")
            for d, c in closes_by_day]


def run_up_to(day, first, value):
    """Weekday bars from `first` to `day`, the close a function of the date."""
    days = [d for d in weekdays(600, first) if d <= day]
    return daily([(d, value(d)) for d in days])


def test_momentum_is_the_year_to_a_month_ago_return_skipping_the_last_month():
    # 100 until a year ago, then 150 until a month ago, then a crash the skip leaves out.
    def value(d):
        return 100.0 if d <= DAY - timedelta(days=365) else 150.0 if d <= DAY - timedelta(days=30) else 50.0
    f = factors_at(run_up_to(DAY, DAY - timedelta(days=400), value), DAY)
    assert f.momentum == pytest.approx(0.5) and f.note is None


def test_volatility_is_sixty_days_of_log_returns_annualised():
    swing = [100.0 * (1.01 if i % 2 else 1.0) for i in range(300)]
    bars = daily(zip(weekdays(300, DAY - timedelta(days=430)), swing))
    f = factors_at(bars, bars[-1].date)
    step = math.log(1.01)
    assert f.volatility == pytest.approx(step * math.sqrt(60 / 59) * math.sqrt(252), rel=1e-3)


def test_bars_after_the_day_are_never_read():
    bars = run_up_to(DAY + timedelta(days=60), DAY - timedelta(days=400), lambda d: 100.0 + d.toordinal() % 7)
    before = factors_at([b for b in bars if b.date <= DAY], DAY)
    wild = [b if b.date <= DAY else replace(b, close=1e9, adj_close=None) for b in bars]
    assert factors_at(wild, DAY) == before
    assert factors_at(bars, DAY).as_of <= DAY


def test_later_bars_do_not_change_the_ranks_on_an_earlier_day():
    first = DAY - timedelta(days=400)
    rising = run_up_to(DAY + timedelta(days=90), first, lambda d: 100.0 + (d - first).days * 0.1)
    falling = [replace(b, symbol="DOWN", close=300.0 - b.close, adj_close=300.0 - b.close) for b in rising]
    cut = {s: [b for b in bars if b.date <= DAY] for s, bars in (("TEST", rising), ("DOWN", falling))}
    later = {"TEST": [b if b.date <= DAY else replace(b, close=1.0, adj_close=1.0) for b in rising], "DOWN": falling}
    assert ranked_at(later, DAY) == ranked_at(cut, DAY)
    assert ranked_at(cut, DAY)["TEST"].momentum_pct == 1.0


def test_too_little_history_is_marked_not_ranked():
    short = run_up_to(DAY, DAY - timedelta(days=200), lambda d: 100.0)
    f = factors_at(short, DAY)
    assert f.momentum is None and f.volatility is not None
    assert "year" in f.note


def test_prices_stopping_well_before_the_day_are_marked():
    old = run_up_to(DAY - timedelta(days=40), DAY - timedelta(days=500), lambda d: 100.0)
    f = factors_at(old, DAY)
    assert f.momentum is None and f.volatility is None and "no price since" in f.note


def test_adjusted_closes_are_used_when_every_bar_has_one():
    # A 2-for-1 split halves the plain close; the adjusted close does not move.
    split = DAY - timedelta(days=100)
    bars = daily([(d, 100.0 if d >= split else 200.0) for d in weekdays(600, DAY - timedelta(days=400)) if d <= DAY],
                 adjusted=lambda d, c: 100.0)
    assert factors_at(bars, DAY).momentum == pytest.approx(0.0)


def test_percentiles_rank_strong_momentum_and_calm_prices_high():
    first = DAY - timedelta(days=400)
    rows = {
        "UP": factors_at(run_up_to(DAY, first, lambda d: 100.0 + (d - first).days), DAY),
        "FLAT": factors_at(run_up_to(DAY, first, lambda d: 100.0 * (1.02 if d.toordinal() % 2 else 1.0)), DAY),
        "DOWN": factors_at(run_up_to(DAY, first, lambda d: 1000.0 - (d - first).days), DAY),
        "NEW": factors_at(run_up_to(DAY, DAY - timedelta(days=100), lambda d: 100.0), DAY),
    }
    ranks = rank(rows)
    assert ranks["UP"].momentum_pct == 1.0 and ranks["DOWN"].momentum_pct == 0.0
    assert ranks["FLAT"].low_vol_pct < ranks["UP"].low_vol_pct
    assert ranks["NEW"].momentum_pct is None and ranks["NEW"].low_vol_pct is not None


def test_a_lone_listing_has_nothing_to_rank_against():
    ranks = rank({"ONE": factors_at(run_up_to(DAY, DAY - timedelta(days=400), lambda d: 100.0), DAY)})
    assert ranks["ONE"].momentum_pct is None and ranks["ONE"].low_vol_pct is None
