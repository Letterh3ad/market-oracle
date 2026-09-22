from oracle.analysts.signals import Direction, Skipped
from oracle.analysts.trend import trend
from tests.analysts.bars import geometric, series


def test_a_steady_rise_is_bullish():
    s = trend(series(geometric(300, 0.001)))
    assert (s.direction, s.score, s.confidence) == (Direction.BULLISH, 1.0, 1.0)


def test_mixed_evidence_scores_by_hand_and_lowers_confidence():
    # 80, then 230 days at 100, then 22 days at 90 (253 bars).
    # close 90 vs sma200 98.9: below (-1); sma50 95.6 vs 98.9: below (-1);
    # 12-1 return 90/80 - 1 = +12.5%, 0.625 of full strength.
    s = trend(series([80.0] + [100.0] * 230 + [90.0] * 22))
    assert s.score == round((-1 - 1 + 0.625) / 3, 4)
    assert (s.direction, s.confidence) == (Direction.BEARISH, round(2 / 3, 4))
    assert dict(s.figures)["sma200"] == 98.9


def test_too_little_history_gives_no_signal_and_says_why():
    s = trend(series(geometric(200, 0.001)))
    assert isinstance(s, Skipped) and "year" in s.reason and "200" in s.reason


def test_a_flat_price_is_neutral_with_no_confidence():
    s = trend(series([50.0] * 300))
    assert (s.direction, s.score, s.confidence) == (Direction.NEUTRAL, 0.0, 0.0)


def test_a_steady_fall_is_bearish_and_its_reason_reads_plainly():
    s = trend(series(geometric(300, -0.001)))
    assert s.direction == Direction.BEARISH
    assert "below its 200-day average" in s.reason and "fell" in s.reason
    assert not any(jargon in s.reason for jargon in ("SMA", "sma", "12-1"))
