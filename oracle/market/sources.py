from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from oracle.market.bars import Bar


class NoData(Exception):
    """The source answered but has nothing for this symbol."""


class SourceFailed(Exception):
    """The source could not be reached or answered with an error."""


class PriceSource(Protocol):
    name: str

    async def daily_bars(self, symbol: str, since: date | None, now: datetime) -> list[Bar]:
        """Bars from `since` (inclusive) to now, or full history when None."""
