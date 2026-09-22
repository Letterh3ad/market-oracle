import pytest

from oracle.analysts.signals import AnalystSignal, Direction, Skipped
from oracle.analysts.technicals import combine, macd_signal, rsi_signal, volatility_signal
from tests.analysts.bars import geometric, series

FLAT_THEN_FALL = [100.0] * 100 + geometric(15, -0.02)
FALL_THEN_RISE = [100.0] * 60 + geometric(21, -0.01)[1:] + geometric(13, 0.015, start=100 * 0.99 ** 20)[1:]


def test_oversold_rsi_reads_as_a_possible_bounce_not_an_instruction():
    s = rsi_signal(series(FLAT_THEN_FALL))
    assert s.direction == Direction.BULLISH and dict(s.figures)["rsi"] < 30
    assert "oversold" in s.reason and "may bounce" in s.reason
    assert "buy" not in s.reason.lower()


def test_rsi_in_its_normal_range_is_neutral():
    s = rsi_signal(series([100.0 * (1.01 if i % 2 else 1.0) for i in range(100)]))
    assert 40 < dict(s.figures)["rsi"] < 60 and s.direction == Direction.NEUTRAL


def test_a_macd_crossover_reports_how_many_days_ago():
    s = macd_signal(series(FALL_THEN_RISE))
    days = dict(s.figures)["days_since_cross"]
    assert days == 8  # the histogram turns positive on the 9th bar from the end
    assert f"{int(days)} trading day" in s.reason and s.score > 0


def test_volatility_never_implies_a_direction_but_says_when_swings_are_unusual():
    calm = [100.0 * (1.002 if i % 2 else 1 / 1.002) for i in range(200)]
    wild = [100.0 * (1.03 if i % 2 else 1 / 1.03) for i in range(20)]
    s = volatility_signal(series(calm + wild))
    assert s.direction == Direction.NEUTRAL and s.score == 0.0
    assert dict(s.figures)["ratio"] > 5 and "bigger than usual" in s.reason


def test_short_histories_are_skipped_with_a_reason():
    short = series(geometric(20, 0.001))
    assert all(isinstance(f(short), Skipped) for f in (rsi_signal, macd_signal, volatility_signal))


def test_gaps_between_dates_do_not_change_the_readings():
    bars = series(FALL_THEN_RISE)
    gapped = [b.__class__(**{**b.__dict__, "date": b.date.replace(year=b.date.year + 1) if i > 40 else b.date})
              for i, b in enumerate(bars)]
    assert macd_signal(bars).score == macd_signal(gapped).score


def _sig(analyst, score, confidence, **figures):
    return AnalystSignal(analyst, "T", series([1.0])[0].date, score, confidence, "", tuple(figures.items()))


def test_combined_technicals_weight_by_confidence_and_damp_for_volatility():
    # (0.9 * 1.0 + -0.5 * 0.5 + 0.6 * 0.5) / (1.0 + 0.5 + 0.5) = 0.475; mean confidence
    # 2/3, cut by 1 / 2 (swings twice usual) to 1/3.
    s = combine([_sig("trend", 0.9, 1.0), _sig("rsi", -0.5, 0.5), _sig("macd", 0.6, 0.5),
                 _sig("volatility", 0.0, 1.0, ratio=2.0)])
    assert (s.analyst, s.score, s.confidence) == ("technicals", 0.475, round(1 / 3, 4))
    assert "trend, RSI and MACD" in s.reason and "2.0x their usual size" in s.reason


def test_combined_with_nothing_to_combine_is_skipped():
    assert isinstance(combine([]), Skipped)
