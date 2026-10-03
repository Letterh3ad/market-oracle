"""UK retail trading costs for strategy tests (research-lab spec 7): the dealing and FX fees of the
user's platform (`[plan]`), half the bid-ask spread by size band, 0.5% stamp duty on buys of UK
shares, FX on every trade of a non-GBP listing. The platform's own yearly fee is the FeeModel's.
Numbers in, numbers out."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from oracle.broker.fills import COSTS, SLIPPAGE_BY_BAND
from oracle.broker.projection import FeeModel

__all__ = ["SIDES", "STAMP_DUTY", "Listing", "TradeCost", "UkCosts", "listing_of", "trade_cost"]

STAMP_DUTY = 0.005
SIDES = ("buy", "sell")


@dataclass(frozen=True)
class Listing:
    symbol: str
    currency: str
    uk_shares: bool
    """A UK company's shares: stamp duty on buys. UK-listed ETFs are exempt (False)."""
    band: str | None = None
    """A MARKET_CAP_BANDS name, or None (the default spread)."""


def listing_of(symbol: str, band: str | None = None) -> Listing:
    """A London (.L) symbol is GBP and UK shares; anything else is taken as USD."""
    uk = symbol.endswith(".L")
    return Listing(symbol, "GBP" if uk else "USD", uk, band)


@dataclass(frozen=True)
class UkCosts:
    fees: FeeModel
    """The user's platform: dealing per trade, FX share, and the platform fee on the pot."""
    half_spreads: Mapping[str, float] = field(default_factory=lambda: dict(SLIPPAGE_BY_BAND))
    """Half the bid-ask spread by band, paid on each trade (the paper accounts' slippage table)."""
    default_half_spread: float = COSTS.slippage
    stamp_duty: float = STAMP_DUTY

    def half_spread(self, band: str | None) -> float:
        return self.half_spreads.get(band or "", self.default_half_spread)

    def rate(self, listing: Listing, side: str) -> float:
        """Every cost of a trade proportional to its value, as a share of it."""
        stamp = self.stamp_duty if listing.uk_shares and side == "buy" else 0.0
        fx = self.fees.fx if listing.currency != "GBP" else 0.0
        return self.half_spread(listing.band) + stamp + fx


@dataclass(frozen=True)
class TradeCost:
    dealing: float
    spread: float
    stamp: float
    fx: float

    @property
    def total(self) -> float:
        return self.dealing + self.spread + self.stamp + self.fx


NONE = TradeCost(0.0, 0.0, 0.0, 0.0)


def trade_cost(c: UkCosts, listing: Listing, side: str, value: float) -> TradeCost:
    """The costs of trading `value` (GBP) of `listing`."""
    if side not in SIDES:
        raise ValueError(f"side {side!r}: buy or sell")
    if value <= 0:
        return NONE
    return TradeCost(c.fees.dealing, value * c.half_spread(listing.band),
                     value * c.stamp_duty if listing.uk_shares and side == "buy" else 0.0,
                     value * c.fees.fx if listing.currency != "GBP" else 0.0)
