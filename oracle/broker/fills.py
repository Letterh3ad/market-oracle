"""Paper fill arithmetic: slippage, FX spread, exchange fees and stamp duty, in whole
shares. Amounts in the listing's currency unless named `cash` (base currency).
Deliberately pessimistic: a paper record should not flatter the strategy."""

from __future__ import annotations

import math
from dataclasses import dataclass

# IBKR fixed-plan approximations, charged in the listing's currency.
US_PER_SHARE, US_MIN, US_CAP = 0.005, 1.0, 0.01
EUROPE = frozenset({
    "Austria", "Belgium", "Czech Republic", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece",
    "Hungary", "Iceland", "Ireland", "Italy", "Latvia", "Lithuania", "Netherlands", "Norway", "Portugal",
    "Spain", "Sweden", "Switzerland",
})
UK_EUROPE_RATE, UK_EUROPE_MIN = 0.0005, 3.0
OTHER_RATE, OTHER_MIN = 0.001, 5.0


@dataclass(frozen=True)
class Costs:
    slippage: float = 0.001
    """Fills this much worse than the open, both ways."""
    fx_spread: float = 0.002
    """Lost on each conversion between the base currency and the listing's."""
    stamp_duty: float = 0.005
    """UK stamp duty on LSE buys."""
    commission: float | None = None
    """A flat fee per trade in the listing's currency, in place of the broker's table."""


COSTS = Costs()


@dataclass(frozen=True)
class Fill:
    shares: int
    price: float
    """Per share, after slippage."""
    fee: float
    stamp: float
    cash: float
    """Change to the account's cash in the base currency: negative for a buy."""
    fx: float
    """Base-currency cost of the FX spread."""
    slippage: float
    """Base-currency cost of slippage."""


def fee(country: str, shares: int, price: float, costs: Costs = COSTS) -> float:
    if costs.commission is not None:
        return costs.commission
    value = shares * price
    if country == "United States":
        return min(max(shares * US_PER_SHARE, US_MIN), value * US_CAP)
    if country == "United Kingdom" or country in EUROPE:
        return max(value * UK_EUROPE_RATE, UK_EUROPE_MIN)
    return max(value * OTHER_RATE, OTHER_MIN)


def buy_fill(cash: float, *, open_price: float, rate: float, country: str, exchange: str,
             costs: Costs = COSTS) -> Fill:
    """The most whole shares `cash` (base currency) buys at the open, costs included.
    `rate` is listing-currency units per base unit; 1.0 for a base-currency listing."""
    spread = costs.fx_spread if rate != 1.0 else 0.0
    per_base = rate * (1 - spread)
    price = open_price * (1 + costs.slippage)
    stamp_rate = costs.stamp_duty if exchange == "LSE" else 0.0
    shares = math.floor(cash * per_base / price)
    while shares > 0:
        total = shares * price * (1 + stamp_rate) + fee(country, shares, price, costs)
        if total <= cash * per_base:
            break
        shares -= 1
    if shares == 0:
        return Fill(0, price, 0.0, 0.0, 0.0, 0.0, 0.0)
    f, stamp = fee(country, shares, price, costs), shares * price * stamp_rate
    total = shares * price + f + stamp
    return Fill(shares, price, f, stamp, -total / per_base, total / per_base - total / rate,
                shares * (price - open_price) / rate)


def sell_fill(shares: int, *, open_price: float, rate: float, country: str, costs: Costs = COSTS) -> Fill:
    spread = costs.fx_spread if rate != 1.0 else 0.0
    price = open_price * (1 - costs.slippage)
    f = fee(country, shares, price, costs)
    proceeds = shares * price - f
    return Fill(shares, price, f, 0.0, proceeds * (1 - spread) / rate, proceeds * spread / rate,
                shares * (open_price - price) / rate)
