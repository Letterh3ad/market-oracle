import pytest

from oracle.universe.listings import Listing, band_of_usd, group_companies, is_receipt, is_receipt_symbol, place_line


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


def test_a_new_line_is_placed_in_the_company_a_regroup_would_give_it():
    grouped = group_companies([Listing("LGLG.F", "LG Electronics Inc.", "equity", "South Korea", "FRA"),
                               Listing("ENL.F", "ENEL S.p.A.", "equity", "Italy", "FRA", isin="IT0003128367")])
    by_name = Listing("066570.KS", "LG Electronics Inc.", "equity", "South Korea", "KSC")
    by_isin = Listing("ENEL.MI", "Enel SpA", "equity", "Italy", "MIL", isin="IT0003128367")
    alone = Listing("277810.KQ", "Rainbow Robotics Co., Ltd.", "equity", "South Korea", "KOE")
    for line in (by_name, by_isin, alone):
        assert place_line(line, grouped) == {x.symbol: x.company_key for x in group_companies([*grouped, line])}[
            line.symbol]


def test_a_new_line_never_joins_by_a_placeholder_name_or_isin():
    placeholders = [Listing(f"P{i}.F", "Deutsche Bank Placeholder", "equity", "Germany", "FRA",
                            isin=f"DE000000000{i:02d}" if i else "XX0000000001") for i in range(31)]
    shared_isin = [Listing(f"S{i}.F", f"Subsidiary {i}", "equity", "Germany", "FRA", isin="XX0000000009")
                   for i in range(31)]
    grouped = group_companies([*placeholders, *shared_isin])
    by_name = Listing("NEW.DE", "Deutsche Bank Placeholder", "equity", "Germany", "FRA")
    by_isin = Listing("NEW2.DE", "Brand New AG", "equity", "Germany", "FRA", isin="XX0000000009")
    for line in (by_name, by_isin):
        placed = place_line(line, grouped)
        assert placed == {x.symbol: x.company_key for x in group_companies([*grouped, line])}[line.symbol]
        assert placed not in {x.company_key for x in grouped}
