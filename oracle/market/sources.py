from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from oracle.market.bars import Bar


class NoData(Exception):
    """The source answered but has nothing for this symbol."""


class SourceFailed(Exception):
    """The source could not be reached or answered with an error."""


@dataclass(frozen=True)
class Instrument:
    symbol: str
    """Yahoo's symbol, as FinanceDatabase gives it."""
    currency: str | None
    """From the universe; only a hint, since FinanceDatabase says GBP for pence lines."""


class PriceSource(Protocol):
    name: str

    async def daily_bars(self, instrument: Instrument, since: date | None, now: datetime) -> list[Bar]:
        """Bars from `since` (inclusive) to now, or full history when None."""
