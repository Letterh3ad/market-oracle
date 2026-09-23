"""What every market data source shares: errors, the request wrapper, and trying
sources in order until one answers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol, TypeVar

import httpx

from oracle.market.bars import Bar
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed

# Depth of a first fetch, for prices and FX alike.
HISTORY_YEARS = 5
HISTORY = timedelta(days=HISTORY_YEARS * 365 + 1)
# Re-fetched on every update, so a bar or rate stored mid-session is replaced by the
# final one.
REFETCH_OVERLAP = timedelta(days=7)


class NoData(Exception):
    """The source answered but has nothing for this symbol."""


class SourceFailed(Exception):
    """The source could not be reached or answered with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        """The HTTP status of an error answer; None when there was no answer."""


class AllFailed(Exception):
    def __init__(self, failures: list[str], no_data: bool = False) -> None:
        super().__init__("; ".join(failures) or "no source enabled")
        self.failures = failures
        self.no_data = no_data
        """Every source answered and none had data: the symbol, not the network, is the
        problem."""


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


class Named(Protocol):
    name: str


S = TypeVar("S", bound=Named)
R = TypeVar("R")


async def first_success(
    sources: Sequence[S], call: Callable[[S], Awaitable[R]], *, empty_is_no_data: bool = False
) -> tuple[R, str]:
    """(result, source name) from the first source that answers. A response that
    cannot be parsed counts as that source failing, so one malformed answer never stops
    the chain."""
    failures = []
    answered_empty = 0
    for source in sources:
        try:
            result = await call(source)
        except NoData as exc:
            failures.append(f"{source.name}: {exc}")
            answered_empty += 1
            continue
        except SourceFailed as exc:
            failures.append(f"{source.name}: {exc}")
            continue
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            failures.append(f"{source.name}: unreadable response ({type(exc).__name__})")
            continue
        if empty_is_no_data and not result:
            failures.append(f"{source.name}: no data")
            answered_empty += 1
            continue
        return result, source.name
    raise AllFailed(failures, no_data=bool(failures) and answered_empty == len(failures))


async def get(
    client: RateLimitedClient,
    source: str,
    url: str,
    params: dict | None = None,
    *,
    headers: dict | None = None,
    not_found: tuple[int, ...] = (404,),
    secret: str | None = None,
) -> httpx.Response:
    """A 200 response, or NoData for `not_found` statuses, or SourceFailed. `secret`
    is masked in any message, since keys travel in query strings."""
    try:
        resp = await client.get(source, url, params=params, headers=headers)
    except (RequestFailed, CircuitOpen) as exc:
        detail = str(exc).removeprefix(f"{source}: ")
        status = exc.status if isinstance(exc, RequestFailed) else None
        raise SourceFailed(detail.replace(secret, "***") if secret else detail, status) from None
    if resp.status_code in not_found:
        raise NoData("no data")
    if resp.status_code != 200:
        raise SourceFailed(f"HTTP {resp.status_code}", resp.status_code)
    return resp


def first_present(*values: float | None) -> float | None:
    """The first value that is not None; unlike `or`, a real 0 counts."""
    return next((v for v in values if v is not None), None)
