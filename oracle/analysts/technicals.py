"""RSI, MACD and volatility signals, and the combined technicals line."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from oracle.analysts.indicators import MIN_BARS, YEAR, closes, macd, rsi, volatility
from oracle.analysts.signals import AnalystSignal, Skipped
from oracle.market.bars import Bar

OVERBOUGHT, OVERSOLD = 70.0, 30.0
COMBINED = ("trend", "rsi", "macd")
_LABELS = {"trend": "trend", "rsi": "RSI", "macd": "MACD"}


def rsi_signal(bars: Sequence[Bar]) -> AnalystSignal | Skipped:
    if short := _too_short("rsi", bars):
        return short
    values = [v for v in rsi(closes(bars)) if v is not None]
    now = values[-1]
    if now >= OVERBOUGHT:
        score = -min(1.0, 0.5 + (now - OVERBOUGHT) / 60)
        reason = (f"RSI is {now:.0f}, overbought (above {OVERBOUGHT:.0f}): after a run of gains the "
                  "price may pull back. A warning, not an instruction.")
    elif now <= OVERSOLD:
        score = min(1.0, 0.5 + (OVERSOLD - now) / 60)
        reason = (f"RSI is {now:.0f}, oversold (below {OVERSOLD:.0f}): after a run of losses the "
                  "price may bounce. A hint, not an instruction.")
    else:
        score = 0.0
        reason = f"RSI is {now:.0f}, inside its normal {OVERSOLD:.0f} to {OVERBOUGHT:.0f} range: no stretch either way."
    # Confidence: how many of the last 5 readings sit in the same zone.
    same = sum(1 for v in values[-5:] if (v >= OVERBOUGHT if score < 0 else v <= OVERSOLD)) if score else 0
    return _signal("rsi", bars, score, same / 5, reason, rsi=now)


def macd_signal(bars: Sequence[Bar]) -> AnalystSignal | Skipped:
    if short := _too_short("macd", bars):
        return short
    line, _, hist = macd(closes(bars))
    signs = [_sign(h) for h in hist if h is not None]
    # Bars since the histogram last changed sign; -1 when it never did in the data.
    days = next((i for i, (a, b) in enumerate(zip(reversed(signs), reversed(signs[:-1]))) if a != b), -1)
    # The crossover (histogram) leads; which side of zero the line is on confirms.
    score = (2 * signs[-1] + _sign(line[-1])) / 3
    confidence = (1.0 if signs[-1] == _sign(line[-1]) else 0.5) if score else 0.0
    side = "above" if signs[-1] > 0 else "below"
    if signs[-1] == 0:
        when = "is level with its own average"
    elif days < 0:
        when = f"has been {side} its own average since the data starts"
    else:
        when = f"crossed {side} its own average {days} trading day{'s' if days != 1 else ''} ago"
    broader = {1: "above the 26-day, so the broader move is rising",
               -1: "below the 26-day, so the broader move is falling",
               0: "level with the 26-day, so the broader move is flat"}[_sign(line[-1])]
    reason = f"Short-term momentum {when}; the 12-day price average is {broader}."
    return _signal("macd", bars, score, confidence, reason, macd=line[-1], histogram=hist[-1], days_since_cross=days)


def volatility_signal(bars: Sequence[Bar]) -> AnalystSignal | Skipped:
    if short := _too_short("volatility", bars):
        return short
    values = [v for v in volatility(closes(bars)) if v is not None]
    now, usual = values[-1], median(values[-YEAR:])
    ratio = now / usual if usual else 1.0
    size = "bigger than usual" if ratio > 1.25 else "smaller than usual" if ratio < 0.8 else "about usual"
    reason = (f"Price swings are {size}: {now:.0%} a year over the last month, against a usual "
              f"{usual:.0%}. This says how risky, not which way.")
    return _signal("volatility", bars, 0.0, 1.0, reason, volatility=now, usual=usual, ratio=ratio)


def combine(signals: Sequence[AnalystSignal], symbol: str) -> AnalystSignal | Skipped:
    """Confidence-weighted average of trend, RSI and MACD, with confidence cut when
    price swings are bigger than usual."""
    parts = [s for s in signals if s.analyst in COMBINED]
    if not parts:
        return Skipped("technicals", symbol, "no technical signals to combine")
    weight = sum(s.confidence for s in parts)
    score = sum(s.score * s.confidence for s in parts) / weight if weight else 0.0
    confidence = weight / len(parts)
    vol = next((dict(s.figures)["ratio"] for s in signals if s.analyst == "volatility"), None)
    names = [_LABELS[s.analyst] for s in parts]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    reason = f"Combines {joined}, each weighted by how sure it is."
    if vol is not None and vol > 1:
        confidence /= vol
        reason += f" Price swings are {vol:.1f}x their usual size, so confidence is cut."
    last = max(parts, key=lambda s: s.as_of)
    return AnalystSignal("technicals", last.symbol, last.as_of, round(score, 4), round(confidence, 4), reason,
                         tuple((s.analyst, s.score) for s in parts) + ((("volatility_ratio", vol),) if vol else ()))


def _too_short(name: str, bars: Sequence[Bar]) -> Skipped | None:
    if len(bars) >= MIN_BARS:
        return None
    return Skipped(name, bars[-1].symbol if bars else "", f"needs {MIN_BARS} trading days of prices, has {len(bars)}")


def _sign(v: float) -> int:
    return (v > 0) - (v < 0)


def _signal(name: str, bars: Sequence[Bar], score: float, confidence: float, reason: str, **figures: float) -> AnalystSignal:
    return AnalystSignal(name, bars[-1].symbol, bars[-1].date, round(score, 4), round(confidence, 4), reason,
                         tuple(figures.items()))
