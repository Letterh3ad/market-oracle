"""Builds sources by their config name. Adding a source means one entry here, its name
in `oracle.config`, and (if paid) its key name."""

from __future__ import annotations

from collections.abc import Sequence

from oracle.config import KEY_NAMES, Keys
from oracle.market import fx
from oracle.market.eodhd import EodhdClient, EodhdPrices
from oracle.market.fundamentals import EodhdFundamentals, FundamentalsSource, YahooFundamentals
from oracle.market.sources import PriceSource
from oracle.market.yahoo import YahooPrices
from oracle.net.client import RateLimitedClient

KEYED = frozenset(KEY_NAMES)
FUNDAMENTALS_SOURCES = ("yahoo", "eodhd")


def missing_key(keys: Keys, name: str) -> bool:
    """A paid source is skipped until its key is set; key names match source names."""
    return name in KEYED and keys.get(name) is None


def price_sources(names: Sequence[str], client: RateLimitedClient, keys: Keys) -> list[PriceSource]:
    build = {
        "yahoo": lambda: YahooPrices(client),
        "eodhd": lambda: EodhdPrices(EodhdClient(client, keys.get("eodhd"))),
    }
    return [build[n]() for n in names if not missing_key(keys, n)]


def fx_sources(names: Sequence[str], client: RateLimitedClient, keys: Keys) -> list[fx.FxSource]:
    build = {
        "yahoo": lambda: fx.YahooFx(client),
        "ecb": lambda: fx.EcbFx(client),
        "frankfurter": lambda: fx.FrankfurterFx(client),
        "eodhd": lambda: fx.EodhdFx(EodhdClient(client, keys.get("eodhd"))),
        "open_er_api": lambda: fx.OpenErFx(client),
    }
    return [build[n]() for n in names if not missing_key(keys, n)]


def fundamentals_sources(names: Sequence[str], client: RateLimitedClient, keys: Keys) -> list[FundamentalsSource]:
    """In the order `names` gives (the `[prices] sources` list), those that have fundamentals."""
    build = {
        "yahoo": lambda: YahooFundamentals(client),
        "eodhd": lambda: EodhdFundamentals(EodhdClient(client, keys.get("eodhd"))),
    }
    return [build[n]() for n in names if n in FUNDAMENTALS_SOURCES and not missing_key(keys, n)]
