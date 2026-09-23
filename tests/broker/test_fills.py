import pytest

from oracle.broker.fills import COSTS, buy_fill, fee, sell_fill

US, UK, EU, OTHER = "United States", "United Kingdom", "Germany", "Japan"


def test_a_buy_by_hand():
    # 1,000 GBP at 1.25 USD per GBP: price 100 * 1.001 = 100.10; spendable
    # 1000 * 1.25 * 0.998 = 1247.50 USD -> 12 shares (1201.20) + 1 USD minimum fee
    # = 1202.20 USD = 1202.20 / 1.2475 = 963.69 GBP.
    f = buy_fill(1000.0, open_price=100.0, rate=1.25, country=US, exchange="NMS", costs=COSTS)
    assert (f.shares, f.price, f.fee) == (12, pytest.approx(100.1), pytest.approx(1.0))
    assert f.cash == pytest.approx(-1202.2 / 1.2475)


def test_the_fee_never_pushes_a_buy_past_its_budget():
    # 10 GBP buys exactly 1 share at 9.99 before costs; the minimum fee must drop it to 0.
    f = buy_fill(10.0, open_price=9.99, rate=1.0, country=UK, exchange="LSE", costs=COSTS)
    assert f.shares == 0


def test_a_base_currency_listing_pays_no_fx_spread():
    f = buy_fill(1000.0, open_price=10.0, rate=1.0, country=UK, exchange="LSE", costs=COSTS)
    assert f.fx == 0.0


@pytest.mark.parametrize("country, shares, price, expected", [
    (US, 100, 50.0, 1.0),        # 100 * 0.005 = 0.50 -> 1.00 minimum
    (US, 1000, 50.0, 5.0),       # 1000 * 0.005
    (US, 10000, 0.10, 10.0),     # 50 per share fee capped at 1% of 1,000
    (UK, 10, 100.0, 3.0),        # 0.05% of 1,000 = 0.50 -> 3 minimum
    (UK, 100, 100.0, 5.0),       # 0.05% of 10,000
    (EU, 10, 100.0, 3.0),
    (OTHER, 10, 100.0, 5.0),     # 0.1% of 1,000 = 1 -> 5 minimum
    (OTHER, 100, 100.0, 10.0),
])
def test_fee_bands(country, shares, price, expected):
    assert fee(country, shares, price, COSTS) == pytest.approx(expected)


def test_a_sell_by_hand():
    # 12 shares at 110 * 0.999 = 109.89 -> 1318.68 USD, less the 1 USD minimum fee =
    # 1317.68 USD, less the 0.2% FX spread, at 1.25 -> 1052.0357 GBP.
    f = sell_fill(12, open_price=110.0, rate=1.25, country=US)
    assert f.cash == pytest.approx(1317.68 * 0.998 / 1.25)
    assert f.stamp == 0.0


def test_stamp_duty_on_lse_buys_only():
    lse = buy_fill(1000.0, open_price=10.0, rate=1.0, country=UK, exchange="LSE", costs=COSTS)
    xetra = buy_fill(1000.0, open_price=10.0, rate=1.0, country=EU, exchange="GER", costs=COSTS)
    assert lse.stamp == pytest.approx(lse.shares * lse.price * 0.005) and lse.stamp > 0
    assert xetra.stamp == 0.0
