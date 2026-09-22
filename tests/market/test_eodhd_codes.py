import pytest

from oracle.market.eodhd import eodhd_code


@pytest.mark.parametrize("symbol, code", [
    ("AAPL", "AAPL.US"),
    ("BRK-B", "BRK-B.US"),
    ("BP.L", "BP.LSE"),
    ("SAP.DE", "SAP.XETRA"),
    ("APC.F", "APC.F"),
    ("MC.PA", "MC.PA"),
    ("ASML.AS", "ASML.AS"),
    ("ENI.MI", "ENI.MI"),
    ("SAN.MC", "SAN.MC"),
    ("NESN.SW", "NESN.SW"),
    ("SHOP.TO", "SHOP.TO"),
    ("7203.T", "7203.TSE"),
    ("0700.HK", "0700.HK"),
    ("005930.KS", "005930.KO"),
    ("RELIANCE.NS", "RELIANCE.NSE"),
    ("BHP.AX", "BHP.AU"),
])
def test_yahoo_symbols_map_to_eodhd_codes(symbol, code):
    assert eodhd_code(symbol) == code


@pytest.mark.parametrize("symbol", ["PETR4.SA", "X.ZZ"])
def test_an_unmapped_exchange_has_no_code(symbol):
    assert eodhd_code(symbol) is None
