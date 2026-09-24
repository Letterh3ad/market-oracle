import pytest

from oracle.universe.listings import band_of_usd, is_receipt, is_receipt_symbol


@pytest.mark.parametrize("name", ["Samsung Electronics GDR", "Infosys ADR", "Apple Inc. BDR", "Apple CDR (CAD Hedged)",
                                  "Tesla Inc CEDEAR", "Vale ADRs", "TOYOTA MOTOR  DRN", "ECOPETROL SADRN"])
def test_depositary_receipts_are_told_apart(name):
    assert is_receipt(name)


@pytest.mark.parametrize("name", ["Apple Inc.", "Cedar Realty Trust", "Adriatic Metals", "BDRX Holdings"])
def test_a_companys_own_shares_are_not_receipts(name):
    assert not is_receipt(name)


@pytest.mark.parametrize("symbol, receipt", [
    ("C1HI34.SA", True), ("E1CO34.SA", True), ("AAPL34.SA", True), ("MSFT35.SA", True), ("BABA33.SA", True),
    ("PETR4.SA", False), ("TAEE11.SA", False), ("VALE3.SA", False), ("AB34.SA", False), ("ABCD34", False),
])
def test_a_sao_paulo_receipt_is_told_by_its_ticker(symbol, receipt):
    assert is_receipt_symbol(symbol) is receipt


@pytest.mark.parametrize("usd, band", [
    (250e9, "Mega Cap"), (200e9, "Mega Cap"), (59e9, "Large Cap"), (10e9, "Large Cap"), (9.9e9, "Mid Cap"),
    (2e9, "Mid Cap"), (1e9, "Small Cap"), (300e6, "Small Cap"), (100e6, "Micro Cap"), (49e6, "Nano Cap"), (0, "Nano Cap"),
])
def test_a_usd_market_cap_falls_in_financedatabases_band(usd, band):
    assert band_of_usd(usd) == band
