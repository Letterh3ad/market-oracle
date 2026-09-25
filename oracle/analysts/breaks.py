"""Breaks in a stored close series. Prices are refetched only from a week before the last
stored bar, so a split between two fetches leaves the older rows on the old basis: a
day-over-day move outside half to double is taken for that, as is a zero or missing close."""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["SPLIT_RATIOS", "first_break", "is_price", "steady"]

SPLIT_RATIOS = (0.5, 2.0)
"""A day-over-day move outside these is taken for a split the stored bars were not re-based for."""


def is_price(close: float | None) -> bool:
    return close is not None and close > 0


def steady(closes: Sequence[float]) -> bool:
    """Every day-over-day move within SPLIT_RATIOS; the closes must all be prices."""
    low, high = SPLIT_RATIOS
    return all(low <= b / a <= high for a, b in zip(closes, closes[1:]))


def first_break(closes: Sequence[float | None]) -> int | None:
    """Index of the first close that is not a price or moved outside SPLIT_RATIOS from the one before."""
    for i, close in enumerate(closes):
        if not is_price(close) or (i and not steady((closes[i - 1], close))):
            return i
    return None
