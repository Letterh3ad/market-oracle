import math

import pytest

from oracle.analysts.risk import correlation, max_drawdown, risk, suggested_size
from oracle.analysts.signals import Direction, Skipped
from tests.analysts.bars import weekdays

SETTINGS = dict(account=10_000.0, max_position=0.10, cash_floor=0.20, target_volatility=0.20)


def alternating(n, move):
    """n closes alternating up and down by a log move, so each return is +-move exactly."""
    return [100.0 * math.exp(move if i % 2 else 0.0) for i in range(n)]


def dated(closes, start=0):
    return list(zip(weekdays(len(closes) + start)[start:], closes))


def test_size_halves_when_volatility_doubles_and_never_exceeds_the_cap():
    assert suggested_size(0.40, **SETTINGS) == pytest.approx(500.0)
    assert suggested_size(0.80, **SETTINGS) == pytest.approx(250.0)
    assert suggested_size(0.05, **SETTINGS) == 1000.0  # calm, but capped at 10% of 10,000


def test_max_drawdown_by_hand_with_its_dates():
    series = dated([100.0, 120.0, 90.0, 110.0, 60.0, 80.0])
    fall, peak, trough = max_drawdown(series)
    assert (fall, peak, trough) == (-0.5, series[1][0], series[4][0])


def test_correlation_uses_only_days_both_traded():
    a = dated(alternating(80, 0.01))
    b = dict(dated(alternating(80, 0.02)))
    # Extra days in b that a never traded must not matter.
    b.update({d: 999.0 for d, _ in dated([0.0] * 5, start=80)})
    assert correlation(a, sorted(b.items())) == pytest.approx(1.0)
    inverse = [(d, 1 / c) for d, c in a]
    assert correlation(a, inverse) == pytest.approx(-1.0)


def test_risk_signal_has_no_direction_and_explains_size_drawdown_and_correlation():
    mine = dated(alternating(300, 0.02))
    s = risk("ME", mine, {"PEER": dated(alternating(300, 0.01))}, **SETTINGS)
    assert s.direction == Direction.NEUTRAL and s.score == 0.0
    f = dict(s.figures)
    vol = 0.02 * math.sqrt(252 * 252 / 251)
    assert f["volatility"] == pytest.approx(vol, rel=1e-3) and f["size"] == pytest.approx(1000 * 0.20 / vol, rel=1e-3)
    assert "PEER" in s.reason and "£" in s.reason and "worst fall" in s.reason.lower()


def test_a_watchlist_of_one_gives_no_correlation_and_says_why():
    s = risk("ME", dated(alternating(300, 0.02)), {}, **SETTINGS)
    assert "no other watched listing" in s.reason


def test_too_little_history_is_skipped():
    assert isinstance(risk("ME", dated(alternating(30, 0.02)), {}, **SETTINGS), Skipped)
