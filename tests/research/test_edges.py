"""Candidate supplier / customer edges: universe names found in a headline or a filing's
sentences, and the keyword rule that types them without a model."""

from oracle.research.edges import (
    EDGE_CHOICES, Listed, NameIndex, common_words, named_pairs, rule_type, sentences_naming,
)


def listed(symbol, name, band="Large Cap"):
    return Listed(symbol, name, band)


INDEX = NameIndex([
    listed("AAPL", "Apple Inc.", "Mega Cap"), listed("2330.TW", "Taiwan Semiconductor Manufacturing Company Limited",
                                                     "Mega Cap"),
    listed("7203.T", "Toyota Motor Corporation", "Mega Cap"), listed("6201.T", "Toyota Industries Corporation"),
    listed("TGT", "Target Corporation"), listed("KO", "The Coca-Cola Company", "Mega Cap"),
    listed("AMD", "AMD, Inc."),
    listed("2317.TW", "Hon Hai Precision Industry Co., Ltd."), listed("MU", "Micron Technology, Inc."),
    listed("GM", "General Motors Company"), listed("NDAQ", "Nasdaq, Inc."),
])
COMPETITORS = ("Our current competitors include: • suppliers of hardware and software for SoC products, such as AMD, "
               "Micron Technology, Inc. and Apple; and • suppliers of memory, such as Hon Hai Precision Industry.")


def test_a_bullet_is_read_with_the_heading_that_introduces_it():
    found = dict(sentences_naming(INDEX, "7203.T", COMPETITORS))
    assert found["2317.TW"] == ("Our current competitors include: and • suppliers of memory, such as Hon Hai "
                                "Precision Industry.")
    assert found["MU"].startswith("Our current competitors include: • suppliers of hardware")


def test_a_competitor_list_is_competitors_whatever_its_bullets_say():
    found = dict(sentences_naming(INDEX, "7203.T", COMPETITORS))
    assert rule_type(found["2317.TW"], "Toyota", "Hon Hai Precision Industry") == "competitor"
    assert rule_type(found["MU"], "Toyota", "Micron") == "competitor"
    assert rule_type("We compete with Micron, a supplier of memory", "Apple", "Micron") == "competitor"


def test_a_which_stock_headline_is_no_tie():
    assert rule_type("Toyota vs. Micron: Which Stock Is a Better Buy?", "Toyota", "Micron") == "none"
    assert rule_type("Toyota versus Micron in 2026", "Toyota", "Micron") == "none"


def test_a_filings_cover_page_and_signatures_name_no_ties():
    text = ("Our common stock is traded on The Nasdaq Global Select Market under the symbol AAPL. "
            "Apple Inc. /s/ Tim Cook Chief Executive Officer, Micron Technology, Inc. director. "
            "The graph compares our return with the S&P 500 and the Nasdaq 100 Index over five years. "
            "We buy memory from Micron Technology, Inc. every year.")
    assert sentences_naming(INDEX, "AAPL", text) == [("MU", "We buy memory from Micron Technology, Inc. every year.")]


def test_a_name_is_found_whole_or_by_its_distinctive_first_word_and_possessives_count():
    assert INDEX.find("Apple's supplier Hon Hai Precision Industry warns") == {"AAPL", "2317.TW"}
    assert INDEX.find("Toyota recalls trucks") == {"7203.T"}, "the clearly biggest company starting so"
    assert INDEX.find("Coca-Cola raises prices") == {"KO"}, "a leading 'the' and a trailing 'company' aside"


def test_common_words_places_and_small_three_letter_lookalikes_are_not_names():
    assert INDEX.find("Retailers miss their target as Taiwan tensions rise") == set()
    assert INDEX.find("General strike hits ports") == set()
    assert INDEX.find("Walmart and Target accounted for 20% of sales.") == set(), "an everyday word is never a name"
    assert INDEX.find("Micron and AMD rally") == {"MU", "AMD"}
    assert INDEX.find("amd shares") == set(), "a three-letter name only as written in capitals"


def test_words_company_descriptions_write_in_lower_case_are_common_and_never_a_first_word_name():
    common = common_words(["An iron ore miner.", "Makes iron bars.", "Sells Toyota cars and iron."], at_least=2)
    assert common == {"iron"}
    index = NameIndex([listed("IRM", "Iron Mountain Incorporated"), listed("QTWO", "Q2 Holdings, Inc.")], common)
    assert index.find("Iron prices slide as China slows") == set()
    assert index.find("Iron Mountain wins data centre deal") == {"IRM"}
    assert index.find("Q2 results beat") == set(), "two characters are never a name"
    small = NameIndex([listed("YUMC", "China Holdings, Inc."), listed("3697.T", "SHIFT, Inc."),
                       listed("JBT", "John Bean Technologies", "Mid Cap")])
    assert small.find("Sony moves production out of China") == set(), "a place is never a trimmed name"
    assert small.find("Memory makers sued over supply shift") == set(), "a one-word name only capitalised"
    assert small.find("Can John Ternus find the next big thing?") == set(), "a mid-cap is never its first word"


def test_pairs_are_the_company_with_each_other_one_named():
    assert named_pairs(INDEX, "7203.T", "Toyota taps Micron for car chips") == {"MU"}
    assert named_pairs(INDEX, "7203.T", "Micron beats estimates") == set(), "the company itself must be named"
    assert named_pairs(INDEX, "AAPL", "Apple and Apple Inc. talk") == set()


def test_a_filing_s_sentences_naming_another_company_are_kept_with_it():
    text = ("We rely on Hon Hai Precision Industry for assembly.  Our products compete with those of many firms. "
            "Sales to Coca-Cola accounted for 11% of net sales in 2025. " + "x" * 700 + ". Micron supplies memory too.")
    found = sentences_naming(INDEX, "AAPL", text)
    assert found == [("2317.TW", "We rely on Hon Hai Precision Industry for assembly."),
                     ("KO", "Sales to Coca-Cola accounted for 11% of net sales in 2025."),
                     ("MU", "Micron supplies memory too.")]


def test_the_rule_types_by_keyword_and_word_order():
    assert rule_type("Apple supplier Hon Hai warns", "Apple", "Hon Hai") == "supplier"
    assert rule_type("Hon Hai to supply Apple with servers", "Apple", "Hon Hai") == "supplier"
    assert rule_type("Hon Hai orders chips from Apple", "Apple", "Hon Hai") == "customer"
    assert rule_type("Sales to Target accounted for 11% of net sales", "Apple", "Target") == "customer"
    assert rule_type("We rely on Hon Hai for final assembly", "Apple", "Hon Hai") == "supplier"
    assert rule_type("Toyota and Micron form joint venture", "Toyota", "Micron") == "partner"
    assert rule_type("Toyota takes on rival Honda in hybrids", "Toyota", "Honda") == "competitor"
    assert rule_type("Toyota and Micron shares rise", "Toyota", "Micron") == "none"
    assert rule_type("Solving our customers' toughest challenges is the mission the board wrote, and Micron agrees",
                     "Apple", "Micron") == "none", "a bare 'customers' far from the name says nothing"
    assert rule_type("Our largest customer, Micron, paid late", "Apple", "Micron") == "customer"
    assert set(EDGE_CHOICES) == {"supplier", "customer", "competitor", "partner", "none"}
