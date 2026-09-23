"""The paper broker's small vocabularies. `StrEnum`, so stored and printed values are the
plain words (a `str, Enum` mixin prints as `Side.BUY` in f-strings)."""

from __future__ import annotations

from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Status(StrEnum):
    PENDING = "pending"
    """Waiting for its next bar, with a note when something else is missing."""
    FILLED = "filled"
    REJECTED = "rejected"


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"
    SKIP = "skip"
