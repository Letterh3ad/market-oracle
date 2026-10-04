"""UK retail costs on hand-built trades: dealing and FX from the user's platform, half the bid-ask
spread by size band, stamp duty on UK shares' buys only, FX only on non-GBP listings."""

import pytest

from oracle.broker.projection import FeeModel
from oracle.broker.uk_costs import Listing, UkCosts, listing_of, trade_cost

PLATFORM = FeeModel(percent=0.0025, cap=None, dealing=5.0, fx=0.0075)
COSTS = UkCosts(PLATFORM)
UK = Listing("BARC.L", "GBP", uk_shares=True, band="Large Cap")
US = Listing("AAPL", "USD", uk_shares=False, band="Mega Cap")
UK_ETF = Listing("VUAG.L", "GBP", uk_shares=False, band=None)


def test_a_uk_share_buy_pays_dealing_half_spread_and_stamp_duty_but_no_fx():
    c = trade_cost(COSTS, UK, "buy", 10_000)
    assert (c.dealing, c.spread, c.stamp, c.fx) == pytest.approx((5.0, 10.0, 50.0, 0.0))
    assert c.total == pytest.approx(65.0)


def test_a_uk_share_sell_pays_no_stamp_duty():
    c = trade_cost(COSTS, UK, "sell", 10_000)
    assert (c.dealing, c.spread, c.stamp, c.fx) == pytest.approx((5.0, 10.0, 0.0, 0.0))


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_a_non_gbp_listing_pays_fx_both_ways_and_never_stamp_duty(side):
    c = trade_cost(COSTS, US, side, 10_000)
    assert (c.dealing, c.spread, c.stamp, c.fx) == pytest.approx((5.0, 10.0, 0.0, 75.0))


def test_a_uk_etf_is_exempt_from_stamp_duty_and_an_unknown_band_pays_the_default_spread():
    c = trade_cost(COSTS, UK_ETF, "buy", 10_000)
    assert (c.dealing, c.spread, c.stamp, c.fx) == pytest.approx((5.0, 10.0, 0.0, 0.0))


@pytest.mark.parametrize("band, half", [("Mega Cap", 0.001), ("Mid Cap", 0.0025), ("Small Cap", 0.005),
                                        ("Micro Cap", 0.01), ("Nano Cap", 0.02)])
def test_the_spread_widens_as_the_size_band_shrinks(band, half):
    c = trade_cost(COSTS, Listing("X", "GBP", False, band), "buy", 1_000)
    assert c.spread == pytest.approx(1_000 * half)


def test_no_platform_fees_leaves_only_the_spread():
    c = trade_cost(UkCosts(FeeModel()), US, "buy", 1_000)
    assert c.total == pytest.approx(c.spread) == pytest.approx(1.0)


def test_a_zero_trade_costs_nothing_and_a_bad_side_is_refused():
    assert trade_cost(COSTS, UK, "buy", 0).total == 0
    with pytest.raises(ValueError):
        trade_cost(COSTS, UK, "short", 100)


def test_a_symbols_listing_reads_its_suffix():
    assert listing_of("BARC.L") == Listing("BARC.L", "GBP", True, None)
    assert listing_of("AAPL", band="Mega Cap") == Listing("AAPL", "USD", False, "Mega Cap")
    assert listing_of("^SP500TR").uk_shares is False


def test_a_london_fund_is_gbp_without_stamp_duty():
    assert listing_of("VUAG.L", fund=True) == Listing("VUAG.L", "GBP", False, None)
