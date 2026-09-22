from oracle.universe.listings import Listing, group_companies


def L(symbol, name, country, exchange, isin=None, delisted=False, type="equity"):
    return Listing(
        symbol=symbol, name=name, type=type, country=country, exchange=exchange,
        isin=isin, delisted=delisted,
    )


def companies(listings):
    """{primary symbol: every member symbol}, independent of how keys are spelled."""
    grouped = group_companies(listings)
    members, primaries = {}, {}
    for x in grouped:
        members.setdefault(x.company_key, set()).add(x.symbol)
        if x.is_primary:
            assert x.company_key not in primaries, f"two primaries for {x.company_key}"
            primaries[x.company_key] = x.symbol
    assert primaries.keys() == members.keys(), "every company needs exactly one primary"
    return {primaries[k]: members[k] for k in members}


def test_listings_sharing_an_isin_are_one_company_with_the_home_board_primary():
    apple = "US0378331005"
    assert companies([
        L("APC.DE", "Apple Inc.", "United States", "GER", apple),
        L("AAPL.MI", "Apple Inc.", "United States", "MIL", apple),
        L("AAPL", "Apple Inc.", "United States", "NMS", apple),
    ]) == {"AAPL": {"AAPL", "APC.DE", "AAPL.MI"}}


def test_listings_without_isins_group_by_name_and_country():
    assert companies([
        L("TOM.F", "Toyota Motor Corporation", "Japan", "FRA"),
        L("TM", "Toyota Motor Corporation", "Japan", "NYQ"),
        L("7203.T", "Toyota Motor Corporation", "Japan", "JPX"),
    ]) == {"7203.T": {"7203.T", "TM", "TOM.F"}}


def test_names_differing_only_in_case_and_punctuation_match():
    assert companies([
        L("BP.L", "BP p.l.c.", "United Kingdom", "LSE"),
        L("BPE.F", "BP PLC", "United Kingdom", "FRA"),
    ]) == {"BP.L": {"BP.L", "BPE.F"}}


def test_a_name_match_joins_listings_that_carry_an_isin_to_ones_that_do_not():
    # Siemens Energy: most listings have no ISIN, a few do.
    assert companies([
        L("ENR.DE", "Siemens Energy AG", "Germany", "GER"),
        L("ENR.MI", "Siemens Energy AG", "Germany", "MIL", "DE000ENER6Y0", delisted=True),
        L("DE000ENER6Y0.SG", "Siemens Energy AG", "Germany", "STU", "DE000ENER6Y0", delisted=True),
    ]) == {"ENR.DE": {"ENR.DE", "ENR.MI", "DE000ENER6Y0.SG"}}


def test_an_isin_joins_listings_whose_country_field_differs():
    # FinanceDatabase gives Siemens' Indian listings India as the country.
    siemens = "DE0007236101"
    assert companies([
        L("SIE.DE", "Siemens Aktiengesellschaft", "Germany", "GER", siemens),
        L("SIE.F", "Siemens Aktiengesellschaft", "Germany", "FRA", siemens),
        L("SIEMENS.NS", "Siemens Aktiengesellschaft", "India", "NSE", siemens),
    ]) == {"SIE.DE": {"SIE.DE", "SIE.F", "SIEMENS.NS"}}


def test_same_name_in_different_countries_stays_separate():
    assert companies([
        L("ABC.L", "Acme Holdings", "United Kingdom", "LSE"),
        L("ABC", "Acme Holdings", "United States", "NYQ"),
    ]) == {"ABC.L": {"ABC.L"}, "ABC": {"ABC"}}


def test_an_active_listing_beats_a_delisted_home_listing():
    assert companies([
        L("OLD.DE", "Oldco AG", "Germany", "GER", delisted=True),
        L("OLD.F", "Oldco AG", "Germany", "FRA"),
    ]) == {"OLD.F": {"OLD.DE", "OLD.F"}}


def test_the_main_board_beats_a_regional_exchange_in_the_home_country():
    assert companies([
        L("SHL.SG", "Siemens Healthineers AG", "Germany", "STU", "US82622J1043"),
        L("SHL.F", "Siemens Healthineers AG", "Germany", "FRA", "US82622J1043"),
        L("SHL.DE", "Siemens Healthineers AG", "Germany", "GER", "US82622J1043"),
        L("SMMNY", "Siemens Healthineers AG", "Germany", "PNK", "US82622J1043"),
    ]) == {"SHL.DE": {"SHL.SG", "SHL.F", "SHL.DE", "SMMNY"}}


def test_without_a_home_listing_the_highest_ranked_exchange_wins():
    assert companies([
        L("XYZ.F", "Cayman Widgets Ltd", "Cayman Islands", "FRA"),
        L("XYZ", "Cayman Widgets Ltd", "Cayman Islands", "NYQ"),
        L("XYZ.L", "Cayman Widgets Ltd", "Cayman Islands", "LSE"),
    ]) == {"XYZ": {"XYZ", "XYZ.F", "XYZ.L"}}


def test_home_country_is_the_one_most_listings_give():
    assert companies([
        L("SIE.DE", "Siemens AG", "Germany", "GER", "DE0007236101"),
        L("SIE.F", "Siemens AG", "Germany", "FRA", "DE0007236101"),
        L("SIEMENS.NS", "Siemens AG", "India", "NSE", "DE0007236101"),
    ]) == {"SIE.DE": {"SIE.DE", "SIE.F", "SIEMENS.NS"}}


def test_equal_listings_break_ties_by_symbol():
    assert companies([
        L("GOOGL", "Alphabet Inc.", "United States", "NMS"),
        L("GOOG", "Alphabet Inc.", "United States", "NMS"),
    ]) == {"GOOG": {"GOOG", "GOOGL"}}


def test_a_nameless_listing_without_an_isin_is_its_own_company():
    assert companies([
        L("AAA.F", "", "Germany", "FRA"),
        L("BBB.F", "", "Germany", "FRA"),
    ]) == {"AAA.F": {"AAA.F"}, "BBB.F": {"BBB.F"}}


def test_an_unknown_exchange_ranks_last_but_still_works():
    assert companies([
        L("Q.ZZZ", "Quux SA", "France", "ZZZ"),
        L("Q.PA", "Quux SA", "France", "PAR"),
    ]) == {"Q.PA": {"Q.PA", "Q.ZZZ"}}


def test_a_placeholder_name_on_dozens_of_one_exchanges_lines_links_nothing():
    # FinanceDatabase names 96 unrelated OTC funds just "ISHARES".
    funds = [L(f"F{i:02d}", "ISHARES", "Brazil", "PNK") for i in range(31)]
    assert len(companies(funds)) == 31


def test_a_company_with_many_preferred_lines_stays_one_company():
    # Freddie Mac has 25 preferred series on OTC.
    lines = [L(f"FMCC{c}", "Federal Home Loan Mortgage Corporation", "United States", "PNK")
             for c in "ABCDEFGHIJKLMNOPQRSTUVWXY"]
    assert len(companies(lines)) == 1


def test_a_fund_never_merges_with_a_company_of_the_same_name():
    assert companies([
        L("ABC", "Acme Global", None, "NYQ"),
        L("ABC.L", "Acme Global", None, "LSE", type="etf"),
    ]) == {"ABC": {"ABC"}, "ABC.L": {"ABC.L"}}


def test_company_keys_do_not_depend_on_input_order():
    rows = [
        L("TM", "Toyota Motor Corporation", "Japan", "NYQ"),
        L("7203.T", "Toyota Motor Corporation", "Japan", "JPX"),
        L("AAPL", "Apple Inc.", "United States", "NMS", "US0378331005"),
    ]
    forward = {x.symbol: x.company_key for x in group_companies(rows)}
    backward = {x.symbol: x.company_key for x in group_companies(list(reversed(rows)))}
    assert forward == backward


def test_a_shared_isin_never_merges_a_fund_with_a_company():
    assert companies([
        L("ABC", "Acme Corp", "United States", "NYQ", "US0000000001"),
        L("ABCF.L", "Acme Tracker", None, "LSE", "US0000000001", type="etf"),
    ]) == {"ABC": {"ABC"}, "ABCF.L": {"ABCF.L"}}
