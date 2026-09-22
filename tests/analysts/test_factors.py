from datetime import date

import pytest

from oracle.analysts.factors import FACTORS, growth, percentile, quality, value
from oracle.analysts.signals import Direction, Skipped
from oracle.market.fundamentals import Fundamentals

DAY = date(2026, 9, 22)


def f(symbol="X", **figures):
    return Fundamentals(symbol, DAY, "test", "USD", **figures)


def test_percentile_counts_ties_as_half():
    # Beats 2 (10, 12), ties 1 (15): (2 + 0.5) / 4.
    assert percentile(15.0, [10.0, 12.0, 15.0, 30.0], higher_is_better=True) == 0.625


def test_a_cheap_company_scores_high_on_value_by_hand():
    # P/E 10 is cheaper than 3 of 4 peers (0.75); P/B 1.0 cheaper than all 4 (1.0).
    # Average 0.875 -> score 2 * 0.875 - 1 = 0.75, both figures agree, 4 peers each.
    peers = [f(pe=p, pb=b) for p, b in ((8.0, 2.0), (12.0, 3.0), (20.0, 1.5), (30.0, 4.0))]
    s = value(f(symbol="ME", pe=10.0, pb=1.0), peers, "industry group Semis")
    assert (s.score, s.direction) == (0.75, Direction.BULLISH)
    assert "cheaper than 3 of 4" in s.reason and "industry group Semis" in s.reason
    assert s.confidence == pytest.approx(4 / 5)  # 4 peers with each figure, 5 wanted


def test_a_loss_maker_is_expensive_not_cheap_on_pe():
    peers = [f(pe=p) for p in (8.0, 12.0, 20.0, 30.0, 40.0)]
    s = value(f(pe=-5.0), peers, "g")
    assert dict(s.figures)["pe_percentile"] == 0.0
    peers_with_loss = peers + [f(pe=-3.0)]
    assert dict(value(f(pe=10.0), peers_with_loss, "g").figures)["pe_percentile"] == pytest.approx(5 / 6, abs=1e-4)


def test_quality_prefers_less_debt_and_growth_uses_revenue():
    peers = [f(profit_margin=m, roe=0.1, debt_to_equity=d, revenue_growth=g)
             for m, d, g in ((0.1, 0.5, 0.05), (0.2, 1.0, 0.1), (0.3, 2.0, 0.2), (0.05, 3.0, 0.0), (0.15, 0.2, 0.3))]
    me = f(profit_margin=0.25, roe=0.1, debt_to_equity=0.3, revenue_growth=0.25)
    q = quality(me, peers, "g")
    assert dict(q.figures)["debt_to_equity_percentile"] == 0.8  # less debt than 4 of 5
    assert growth(me, peers, "g").score == pytest.approx(2 * 0.8 - 1)


def test_a_figure_missing_for_the_company_is_skipped_and_none_left_is_no_signal():
    peers = [f(pe=p, pb=1.0) for p in (8.0, 12.0, 20.0, 30.0, 40.0)]
    s = value(f(pb=1.0), peers, "g")
    assert "pe_percentile" not in dict(s.figures) and "pb_percentile" in dict(s.figures)
    skipped = growth(f(pe=10.0), peers, "g")
    assert isinstance(skipped, Skipped) and "revenue growth" in skipped.reason


def test_few_peers_with_a_figure_lowers_confidence_and_says_so():
    s = value(f(pe=10.0), [f(pe=p) for p in (12.0, 15.0)], "g")
    assert s.confidence == pytest.approx(2 / 5) and "only 2 peers" in s.reason


def test_every_factor_has_a_plain_english_meaning():
    assert {x.name for x in FACTORS} == {"value", "quality", "growth"}
    assert all(len(x.meaning) > 30 for x in FACTORS)
