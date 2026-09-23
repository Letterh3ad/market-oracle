from datetime import date

import pytest

from oracle.analysts.signals import AnalystSignal
from oracle.broker.baseline import baseline_lean

DAY = date(2026, 9, 21)


def sig(analyst, score, confidence):
    return AnalystSignal(analyst, "X", DAY, score, confidence, "", ())


def test_all_four_agreeing_by_hand():
    # Weighted score (0.8*1 + 0.4*0.5 + 0.6*1 + 0.2*0.5) / 3 = 1.7 / 3 = 0.5667 -> buy.
    # Probability 0.5 + 0.5 * (1 + 0.5 + 1 + 0.5) / 4 = 0.875.
    lean = baseline_lean("X", [sig("technicals", 0.8, 1.0), sig("value", 0.4, 0.5), sig("quality", 0.6, 1.0),
                               sig("growth", 0.2, 0.5)])
    assert (lean.label, lean.source, lean.as_of) == ("buy", "baseline", DAY)
    assert lean.probability == pytest.approx(0.875)


def test_strong_labels_and_disagreement_lowers_probability():
    lean = baseline_lean("X", [sig("technicals", -0.9, 1.0), sig("value", -0.8, 1.0), sig("quality", 0.5, 0.5)])
    # Score (-0.9 - 0.8 + 0.25) / 2.5 = -0.58 -> sell; agreeing confidence 2 of 4 -> 0.75.
    assert lean.label == "sell" and lean.probability == pytest.approx(0.75)
    assert baseline_lean("X", [sig("technicals", 0.9, 1.0), sig("value", 0.9, 1.0)]).label == "strong buy"


def test_technicals_alone_still_lean_with_lower_probability():
    lean = baseline_lean("X", [sig("technicals", 0.5, 0.8)])
    assert lean.label == "buy" and lean.probability == pytest.approx(0.5 + 0.5 * 0.8 / 4)


def test_no_technicals_means_no_lean():
    assert baseline_lean("X", [sig("value", 0.9, 1.0)]) is None


@pytest.mark.parametrize("score, label", [(-0.6, "strong sell"), (-0.2, "sell"), (0.19, "hold"), (0.2, "buy"),
                                          (0.6, "strong buy")])
def test_band_edges_match_signal_direction(score, label):
    assert baseline_lean("X", [sig("technicals", score, 1.0)]).label == label
