"""Companies FinanceDatabase files under the wrong sector (or country), put right by hand.

Keyed by company key. Read by discovery (the universe screen, the business-fit check, name
matching) and by discovery's peers, never by research or the factor analysts while the research
loop is frozen (DECISIONS 2026-09-25). Sources: the session 15 reviews and a scan of names
saying Bank, Pharma, Semiconductor, Mining or Software filed under another sector, Small Cap and
up (2026-09-25); each checked against the company's own description of its business."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TypeVar


@dataclass(frozen=True)
class Correction:
    name: str
    """Who it is, for whoever edits the table."""
    sector: str
    industry_group: str
    industry: str
    country: str | None = None
    """Only when the universe's is wrong too."""

    def values(self) -> dict[str, str]:
        found = {"sector": self.sector, "industry_group": self.industry_group, "industry": self.industry}
        return found | ({"country": self.country} if self.country else {})


_DEFENCE = ("Industrials", "Capital Goods", "Aerospace & Defense")
_ELECTRICAL = ("Industrials", "Capital Goods", "Electrical Equipment")
_HOUSEBUILDER = ("Consumer Discretionary", "Consumer Durables & Apparel", "Household Durables")
_FOOD = ("Consumer Staples", "Food, Beverage & Tobacco", "Food Products")
_BANK = ("Financials", "Banks", "Banks")
_BROKER = ("Financials", "Diversified Financials", "Capital Markets")
_PHARMA = ("Health Care", "Pharmaceuticals, Biotechnology & Life Sciences", "Pharmaceuticals")
_BIOTECH = ("Health Care", "Pharmaceuticals, Biotechnology & Life Sciences", "Biotechnology")
_MINING = ("Materials", "Materials", "Metals & Mining")


def _c(name: str, classes: tuple[str, str, str], country: str | None = None) -> Correction:
    return Correction(name, *classes, country)


CORRECTIONS: dict[str, Correction] = {
    # Session 15 reviews.
    "isin:DE0007030009|equity": _c("Rheinmetall AG", _DEFENCE, "Germany"),
    "isin:FR0000121972|equity": _c("Schneider Electric S.E.", _ELECTRICAL),
    "isin:FR0010307819|equity": _c("Legrand SA", _ELECTRICAL),
    "isin:DE000ENER6Y0|equity": _c("Siemens Energy AG", _ELECTRICAL),
    "name:arista networks inc|United States|equity": _c(
        "Arista Networks, Inc.", ("Information Technology", "Technology Hardware & Equipment",
                                  "Communications Equipment")),
    "name:danone sa|France|equity": _c("Danone S.A. (Paris)", _FOOD),
    "name:danone sa|United States|equity": _c("Danone S.A. (US OTC)", _FOOD, "France"),
    "name:barratt developments plc|United Kingdom|equity": _c("Barratt Developments plc", _HOUSEBUILDER),
    "name:persimmon plc|United Kingdom|equity": _c("Persimmon Plc", _HOUSEBUILDER),
    "name:taylor wimpey plc|United Kingdom|equity": _c("Taylor Wimpey plc", _HOUSEBUILDER),
    "isin:GB0000904986|equity": _c("Bellway p.l.c.", _HOUSEBUILDER),
    "name:vistry group plc|United Kingdom|equity": _c("Vistry Group PLC", _HOUSEBUILDER),
    # The name scan: banks and brokers.
    "name:rhb bank berhad|United States|equity": _c("RHB Bank Berhad", _BANK, "Malaysia"),
    "name:alliance bank malaysia berhad|United States|equity": _c("Alliance Bank Malaysia Berhad", _BANK,
                                                                   "Malaysia"),
    "name:bank islam malaysia berhad|United States|equity": _c("Bank Islam Malaysia Berhad", _BANK, "Malaysia"),
    "name:hong leong bank berhad|United States|equity": _c("Hong Leong Bank Berhad", _BANK, "Malaysia"),
    "isin:MYL1155OO000|equity": _c("Malayan Banking Berhad", _BANK, "Malaysia"),
    "isin:MYL1295OO004|equity": _c("Public Bank Berhad", _BANK, "Malaysia"),
    "name:kenanga investment bank bhd|United States|equity": _c("Kenanga Investment Bank Bhd.", _BROKER,
                                                                 "Malaysia"),
    "name:mwb fairtrade wertpapierhandelsbank ag|United States|equity": _c(
        "mwb fairtrade Wertpapierhandelsbank AG", _BROKER, "Germany"),
    "name:vpe wertpapierhandelsbank ag|United States|equity": _c("vPE Wertpapierhandelsbank AG", _BROKER,
                                                                  "Germany"),
    "name:tradegate ag wertpapierhandelsbank|United States|equity": _c("Tradegate AG Wertpapierhandelsbank",
                                                                        _BROKER, "Germany"),
    "name:bff bank spa|United Kingdom|equity": _c("BFF Bank S.p.A.", _BANK, "Italy"),
    "isin:US2036071064|equity": _c("Community Bank System, Inc.", _BANK),
    "isin:US1972361026|equity": _c("Columbia Banking System, Inc.", _BANK),
    "isin:US7620932019|equity": _c("Rhinebeck Bancorp, Inc.", _BANK),
    # Drug makers.
    "name:wanbangde pharmaceutical holding group co ltd|China|equity": _c(
        "Wanbangde Pharmaceutical Holding Group", _PHARMA),
    "name:yanan bicon pharmaceutical listed company|China|equity": _c("YanAn Bicon Pharmaceutical", _PHARMA),
    "name:yantai dongcheng pharmaceutical group coltd|China|equity": _c("Yantai Dongcheng Pharmaceutical",
                                                                        _PHARMA),
    "name:beijing aosaikang pharmaceutical co ltd|China|equity": _c("Beijing Aosaikang Pharmaceutical", _PHARMA),
    "name:shandong jincheng pharmaceutical group co ltd|China|equity": _c(
        "Shandong Jincheng Pharmaceutical Group", _PHARMA),
    "name:changjiang pharmaceutical group co ltd|China|equity": _c("Changjiang Pharmaceutical Group", _PHARMA),
    "name:cspc innovation pharmaceutical co ltd|China|equity": _c("CSPC Innovation Pharmaceutical", _PHARMA),
    "name:tibet rhodiola pharmaceutical holding co class a|United States|equity": _c(
        "Tibet Rhodiola Pharmaceutical Holding", _PHARMA, "China"),
    "name:guizhou salvage pharmaceutical coltd|China|equity": _c("Guizhou Salvage Pharmaceutical", _PHARMA),
    "name:duopharma biotech berhad|United States|equity": _c("Duopharma Biotech Berhad", _PHARMA, "Malaysia"),
    "name:dechra pharmaceuticals plc|Philippines|equity": _c("Dechra Pharmaceuticals PLC", _PHARMA,
                                                             "United Kingdom"),
    "name:medexus pharmaceuticals inc|Australia|equity": _c("Medexus Pharmaceuticals Inc.", _PHARMA, "Canada"),
    "name:rhythm pharmaceuticals inc|United States|equity": _c("Rhythm Pharmaceuticals, Inc.", _BIOTECH),
    # Chips, mining, software.
    "name:hg semiconductor limited|China|equity": _c(
        "HG Semiconductor Limited", ("Information Technology", "Semiconductors & Semiconductor Equipment",
                                     "Semiconductors & Semiconductor Equipment")),
    "name:nicola mining|United States|equity": _c("Nicola Mining", _MINING, "Canada"),
    "name:mitsui mining smelting co ltd|Japan|equity": _c("Mitsui Mining & Smelting Co., Ltd.", _MINING),
    "name:taketwo interactive software inc|United States|equity": _c(
        "Take-Two Interactive Software, Inc.", ("Communication Services", "Media & Entertainment",
                                                "Entertainment")),
}

T = TypeVar("T")


def corrected(row: T, corrections: dict[str, Correction] = CORRECTIONS) -> T:
    """The row (a ListingProfile, a SearchHit) with its company's correction applied, if any."""
    if (c := corrections.get(getattr(row, "company_key", None))) is None:
        return row
    own = {f.name for f in fields(row)}
    return replace(row, **{k: v for k, v in c.values().items() if k in own})
