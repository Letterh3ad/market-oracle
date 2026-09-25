from dataclasses import replace

import pytest

from oracle.market.yahoo import YahooProfile
from oracle.universe.backfill import (
    BACKFILL, apply_backfill, inherit_classification, is_closed_end_fund, listing_from_profile,
)
from oracle.universe.listings import Listing, group_companies

LGES = YahooProfile("373220.KS", "LG Energy Solution, Ltd.", "EQUITY", "KSC", "KRW", 82_250_997_694_464.0,
                    "South Korea", "Industrials", "Electrical Equipment & Parts", "https://www.lgensol.com", "Makes cells.")
KRW_PER_USD = 1390.0


def test_a_profile_becomes_a_backfilled_home_line():
    x = listing_from_profile(LGES, market="KOSPI Stock Market", units_per_usd=KRW_PER_USD)
    assert x == Listing(
        symbol="373220.KS", name="LG Energy Solution, Ltd.", type="equity", country="South Korea", exchange="KSC",
        market="KOSPI Stock Market", currency="KRW", sector="Industrials", industry="Electrical Equipment & Parts",
        market_cap="Large Cap", website="https://www.lgensol.com", summary="Makes cells.", source=BACKFILL,
    )


@pytest.mark.parametrize("yahoo, universe", [
    ("Technology", "Information Technology"), ("Consumer Cyclical", "Consumer Discretionary"),
    ("Consumer Defensive", "Consumer Staples"), ("Financial Services", "Financials"), ("Healthcare", "Health Care"),
    ("Basic Materials", "Materials"), ("Industrials", "Industrials"), ("Something New", None),
])
def test_yahoo_sectors_are_named_as_the_universe_names_them(yahoo, universe):
    assert listing_from_profile(replace(LGES, sector=yahoo), None, KRW_PER_USD).sector == universe


def test_a_pence_market_cap_is_in_pounds_and_the_country_can_come_from_the_exchange():
    bt = YahooProfile("BT-A.L", "BT Group plc", "EQUITY", "LSE", "GBp", 19_056_902_144.0, None, None, None, None, None)
    x = listing_from_profile(bt, "London Stock Exchange", units_per_usd=0.75)
    assert (x.currency, x.market_cap, x.country) == ("GBp", "Large Cap", "United Kingdom")


def test_no_rate_or_no_market_cap_leaves_the_band_unknown():
    assert listing_from_profile(LGES, None, None).market_cap is None
    assert listing_from_profile(replace(LGES, market_cap=None), None, KRW_PER_USD).market_cap is None


FD = [
    Listing("LGLG.F", "LG Electronics Inc.", "equity", "South Korea", "FRA", isin="US50186Q2021",
            sector="Consumer Discretionary", industry_group="Consumer Durables & Apparel", industry="Household Durables"),
    Listing("008110.KS", "Samsung C&T Corporation", "equity", "South Korea", "KSC", market_cap="Micro Cap"),
]
LGE = Listing("066570.KS", "LG Electronics Inc.", "equity", "South Korea", "KSC", currency="KRW",
              sector="Information Technology", industry="Consumer Electronics", market_cap="Large Cap", source=BACKFILL)


def test_backfilled_lines_join_the_listings_and_corrections_apply():
    out = apply_backfill(FD, [LGE], {"008110.KS": {"delisted": True}})
    assert [x.symbol for x in out] == ["LGLG.F", "008110.KS", "066570.KS"]
    assert out[1].delisted and out[1].name == "Samsung C&T Corporation"


def test_a_line_the_universe_gained_itself_is_not_added_twice():
    fd_gained = replace(LGE, symbol="066570.ks", source=None)
    out = apply_backfill([*FD, fd_gained], [LGE], {})
    assert [x.symbol for x in out] == ["LGLG.F", "008110.KS", "066570.ks"]


def test_a_correction_for_a_line_no_longer_listed_is_ignored():
    assert apply_backfill(FD[:1], [], {"GONE.KS": {"delisted": True}}) == FD[:1]


def test_a_backfilled_home_line_groups_by_name_and_takes_the_companys_classification():
    grouped = inherit_classification(group_companies(apply_backfill(FD, [LGE], {})))
    lge = {x.symbol: x for x in grouped}
    assert lge["066570.KS"].company_key == lge["LGLG.F"].company_key
    assert lge["066570.KS"].is_primary
    assert (lge["066570.KS"].sector, lge["066570.KS"].industry) == ("Consumer Discretionary", "Household Durables")


def test_a_delisted_line_lends_no_classification():
    dead = replace(FD[0], delisted=True)
    grouped = inherit_classification(group_companies([dead, LGE]))
    assert {x.symbol: x for x in grouped}["066570.KS"].sector == "Information Technology"


@pytest.mark.parametrize("name, exchange, industry, summary", [
    # Yahoo gives London investment trusts no business profile at all.
    ("Invesco Asia Dragon Trust Ord", "LSE", None, None),
    ("JPMorgan Emerging Markets Growth & Income plc.", "LSE", None, None),
    ("Alliance Witan Ord", "LSE", None, None),
    ("Seraphim Space Investment Trust Ord", "LSE", "Asset Management", "Invests in space companies."),
    ("Scottish Mortgage", "LSE", "Asset Management", "Scottish Mortgage is an investment trust managed by Baillie."),
    ("Fidelity Emerging Markets Limited", "LSE", "Asset Management",
     "Fidelity Emerging Markets Limited is a closed ended equity mutual fund launched by Genesis."),
    ("Foresight Environmental Infrastructure Limited", "LSE", "Asset Management",
     "John Laing Environmental Assets Group Limited is a fund of John Laing Capital Management Limited."),
    ("Adams Diversified Equity Fund", "NYQ", None, None),
    ("Some Growth Trust", "NMS", None, None),
])
def test_closed_end_funds_are_told_apart(name, exchange, industry, summary):
    assert is_closed_end_fund(name, exchange, industry, summary)


@pytest.mark.parametrize("name, exchange, industry, summary", [
    ("Shawbrook Group PLC", "LSE", "Banks - Regional", "Shawbrook Group PLC provides lending."),
    ("Sumitomo Mitsui Trust Group, Inc.", "JPX", "Banks - Regional", "Operates as a trust bank in Japan."),
    ("Link Real Estate Investment Trust", "HKG", "REIT - Retail", "A real estate investor."),
    ("ICG plc", "LSE", "Asset Management",
     "ICG plc is a private equity firm specializing in direct and fund of fund investments."),
    ("ISU Specialty Chemical Co., Ltd.", "KSC", None, None),
    ("Hua Nan Financial Holdings Co., Ltd.", "TAI", "Banks - Regional",
     "Hua Nan issues beneficiary certificates to raise investment trust funds and making securities investments."),
    ("Northern Trust Corporation", "NMS", "Asset Management", "Provides wealth management services."),
])
def test_companies_are_not_taken_for_closed_end_funds(name, exchange, industry, summary):
    assert not is_closed_end_fund(name, exchange, industry, summary)
