"""Builds sources by their config name. Adding a source means an entry in each builder
for the data it serves, its name in `oracle.config`, and (if paid) its key name."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from oracle.config import KEY_NAMES, Keys
from oracle.market import fx
from oracle.market.eodhd import EodhdClient, EodhdPrices
from oracle.market.fundamentals import EodhdFundamentals, FundamentalsSource, YahooFundamentals
from oracle.market.sources import PriceSource
from oracle.market.yahoo import YahooPrices
from oracle.net.client import RateLimitedClient

T = TypeVar("T")


def missing_key(keys: Keys, name: str) -> bool:
    """A paid source is skipped until its key is set; key names match source names."""
    return name in KEY_NAMES and keys.get(name) is None


def price_sources(names: Sequence[str], client: RateLimitedClient, keys: Keys) -> list[PriceSource]:
    return _enabled(names, keys, {
        "yahoo": lambda: YahooPrices(client),
        "eodhd": lambda: EodhdPrices(_eodhd(client, keys)),
    })


def fx_sources(names: Sequence[str], client: RateLimitedClient, keys: Keys) -> list[fx.FxSource]:
    return _enabled(names, keys, {
        "yahoo": lambda: fx.YahooFx(client),
        "ecb": lambda: fx.EcbFx(client),
        "frankfurter": lambda: fx.FrankfurterFx(client),
        "eodhd": lambda: fx.EodhdFx(_eodhd(client, keys)),
        "open_er_api": lambda: fx.OpenErFx(client),
    })


def fundamentals_sources(names: Sequence[str], client: RateLimitedClient, keys: Keys) -> list[FundamentalsSource]:
    """In the order `names` gives (the `[prices] sources` list), those that have fundamentals."""
    build = {
        "yahoo": lambda: YahooFundamentals(client),
        "eodhd": lambda: EodhdFundamentals(_eodhd(client, keys)),
    }
    return _enabled([n for n in names if n in build], keys, build)


def _enabled(names: Sequence[str], keys: Keys, build: dict[str, Callable[[], T]]) -> list[T]:
    return [build[n]() for n in names if not missing_key(keys, n)]


def _eodhd(client: RateLimitedClient, keys: Keys) -> EodhdClient:
    return EodhdClient(client, keys.get("eodhd"))
