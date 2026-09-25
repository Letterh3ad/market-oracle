"""Closed-end funds told apart from companies, on Yahoo's own names, industries and summaries
(read from the live universe 2026-09-25; long summaries cut after the telling sentence)."""

import pytest

from oracle.universe.backfill import closed_end_fund

# The 12 London lines the first live backfill added: all but two have no Yahoo business profile.
TRUSTS = [
    ("Alliance Witan Ord", None, None),
    ("The European Smaller Companies Trust PLC", None, None),
    ("Fidelity Emerging Markets Limited", "Asset Management",
     "Fidelity Emerging Markets Limited is a closed ended equity mutual fund launched by Genesis Asset Managers, "
     "LLP. The fund is managed by Genesis Investment Management, LLP."),
    ("Foresight Environmental Infrastructure Limited", "Asset Management",
     "John Laing Environmental Assets Group Limited is a fund of John Laing Capital Management Limited."),
    ("The Global Smaller Companies Trust Ord", None, None),
    ("Invesco Asia Dragon Trust Ord", None, None),
    ("JPMorgan Emerging Markets Growth & Income plc.", None, None),
    ("JPMorgan European Discovery Ord", None, None),
    ("JPMorgan European Growth & Income Ord", None, None),
    ("Pantheon Infrastructure Ord", None, None),
    ("Patria Private Equity Trust", None, None),
    ("Seraphim Space Investment Trust Ord", None, None),
]

COMPANIES = [
    ("Simon Property Group, Inc.", "Equity Real Estate Investment Trusts (REITs)",
     "Simon is a real estate investment trust engaged in the ownership of premier shopping, dining, entertainment "
     "and mixed-use destinations and an S&P 100 company (Simon Property Group, NYSE: SPG)."),
    ("SEGRO Plc", "Equity Real Estate Investment Trusts (REITs)",
     "SEGRO is a UK Real Estate Investment Trust (REIT), and a leading owner, manager and developer of modern "
     "warehouses and light industrial property."),
    # Yahoo gives Tritax no industry: its summary alone must keep it a company.
    ("Tritax Big Box REIT plc", None,
     "Tritax Big Box REIT plc is a real estate investment trust (REIT) that invests in large logistics warehouses, "
     "known as \"big boxes,\" in the United Kingdom."),
    ("BlackRock, Inc.", "Capital Markets",
     "BlackRock, Inc. is a publicly owned investment manager. The firm primarily provides its services to "
     "institutional, intermediary, and individual investors. It also launches and manages open-end and closed-end "
     "mutual funds, offshore funds, unit trusts, and alternative investment vehicles including structured funds."),
    ("Canadian Tire Corporation, Limited", "Specialty Retail",
     "Canadian Tire Corporation, Limited provides a range of retail goods and services in Canada. The company "
     "operates in three segments: Retail, CT REIT, and Financial Services. The CT REIT segment operates as a "
     "closed-end real estate investment trust that holds a portfolio of properties comprising Canadian Tire stores."),
    ("E-L Financial Corporation Limited", "Insurance",
     "E-L Financial Corporation Limited operates as an investment and insurance holding company in Canada. The "
     "company owns investments in equities and fixed income securities directly and indirectly through common "
     "shares, investment funds, closed-end investment companies, and limited partnership and other private companies."),
    ("Golub Capital BDC, Inc.", "Capital Markets",
     "Golub Capital BDC, Inc. is a business development company and operates as an externally managed closed-end "
     "non-diversified management investment company."),
    ("Federal Realty Investment Trust", None,
     "Federal Realty is a recognized leader in the ownership, operation and redevelopment of high-quality "
     "retail-based properties located primarily in major coastal markets."),
    ("PennyMac Mortgage Investment Trust", None,
     "PennyMac Mortgage Investment Trust, a specialty finance company, invests primarily in mortgage-related assets "
     "in the United States. The company qualifies as a real estate investment trust for federal income tax purposes."),
    ("BT Group", None, None),
    ("Hua Nan Financial Holdings Co., Ltd.", "Banks - Regional",
     "Hua Nan Financial Holdings Co., Ltd., together with its subsidiaries, provides financial services in Taiwan. "
     "It issues beneficiary certificates to raise investment trust funds and making securities investments."),
    ("ICG plc", "Asset Management",
     "ICG plc is a private equity firm specializing in direct and fund of fund investments."),
    ("Sumitomo Mitsui Trust Group, Inc.", "Banks - Regional",
     "Sumitomo Mitsui Trust Group, Inc. operates as a trust bank in Japan and internationally."),
    ("ISU Specialty Chemical Co., Ltd.", None, None),
    # Asset managers Yahoo describes by the funds they run.
    ("Platinum Investment Management Limited", "Capital Markets",
     "Platinum Asset Management is a publicly owned hedge fund sponsor. The firm primarily provides its services to "
     "pooled investment vehicles."),
    ("N1 Technologies Inc.", "Chemicals",
     "N1 Technologies Inc. is an employee owned hedge fund sponsor. The firm provides its services to individuals, "
     "trusts, investment companies, and retirement accounts."),
    ("Canso Select Opportunities Corporation", "Capital Markets",
     "Canso Select Opportunities Corporation is a publicly owned hedge fund manager. The firm invests in the public "
     "equity and fixed Income markets of Canada and across the globe."),
    ("Alpine Select AG", "Capital Markets",
     "Alpine Select AG is a publicly owned hedge fund manager. It provides its services to institutional and "
     "individual investors."),
    ("INTERTRUST NV INTERTRUST GROUP", None,
     "INTERTRUST NV is a provider of corporate and fund services. The company offers trust, management, and "
     "administrative services to multinational corporations, private equity firms, and other clients."),
]


@pytest.mark.parametrize("name, industry, summary", TRUSTS)
def test_the_london_trusts_added_are_closed_end_funds(name, industry, summary):
    assert closed_end_fund(name, industry, summary)


@pytest.mark.parametrize("name, industry, summary", COMPANIES)
def test_reits_and_companies_that_mention_funds_are_not(name, industry, summary):
    assert closed_end_fund(name, industry, summary) is None


def test_each_match_says_why():
    assert closed_end_fund("Invesco Asia Dragon Trust Ord", None, None) == "no Yahoo profile, named Trust"
    assert closed_end_fund("Seraphim Space Investment Trust Ord", "Asset Management", "Invests in space.") == (
        "named an investment trust")
    assert closed_end_fund(*TRUSTS[2]) == "Yahoo: is a closed ended equity mutual fund"
