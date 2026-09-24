import pytest

from oracle.universe.listings import is_receipt


@pytest.mark.parametrize("name", ["Samsung Electronics GDR", "Infosys ADR", "Apple Inc. BDR", "Apple CDR (CAD Hedged)",
                                  "Tesla Inc CEDEAR", "Vale ADRs", "TOYOTA MOTOR  DRN"])
def test_depositary_receipts_are_told_apart(name):
    assert is_receipt(name)


@pytest.mark.parametrize("name", ["Apple Inc.", "Cedar Realty Trust", "Adriatic Metals", "BDRX Holdings"])
def test_a_companys_own_shares_are_not_receipts(name):
    assert not is_receipt(name)
